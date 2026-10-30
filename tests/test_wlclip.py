# -*- coding: utf-8 -*-
"""Wayland 剪贴板兜底：uri-list 格式、会话探测、调用 wl-copy 的方式。

这一层出错的表现很隐晦 —— X11 那边一切正常，只有纯 Wayland 应用粘不出来。
所以格式细节（CRLF、末尾换行、转义）都用断言钉住。
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wpsclip import wlclip  # noqa: E402


class UriListTest(unittest.TestCase):
    def test_crlf_and_trailing_newline(self):
        # 规范要求 CRLF，且末尾也要有一个 —— 少了部分程序会吞掉最后一行
        self.assertEqual(wlclip.uri_list(['/a.docx']), 'file:///a.docx\r\n')
        self.assertEqual(wlclip.uri_list(['/a.docx', '/b.docx']),
                         'file:///a.docx\r\nfile:///b.docx\r\n')

    def test_spaces_escaped_and_roundtrip(self):
        import urllib.parse
        raw = '/home/u/我的 文档.docx'
        out = wlclip.uri_list([raw])
        # 空格必须转义：URI 里出现裸空格，部分接收方会直接判为非法
        self.assertNotIn(' ', out)
        self.assertIn('%20', out)
        # 解回来必须还是原路径 —— 编码对不对，就看这一条
        self.assertEqual(urllib.parse.unquote(out.strip()[7:]), raw)

    def test_slash_not_escaped(self):
        self.assertEqual(wlclip.uri_list(['/a/b/c.pdf']), 'file:///a/b/c.pdf\r\n')

    def test_empty(self):
        self.assertEqual(wlclip.uri_list([]), '\r\n')


class SessionTest(unittest.TestCase):
    def setUp(self):
        wlclip.reset()
        self._env = {k: os.environ.get(k)
                     for k in ('XDG_SESSION_TYPE', 'WAYLAND_DISPLAY')}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        wlclip.reset()

    def test_x11_is_not_wayland(self):
        os.environ['XDG_SESSION_TYPE'] = 'x11'
        os.environ['DISPLAY'] = ':0'
        self.assertFalse(wlclip.session_is_wayland())

    def test_session_type_wins(self):
        os.environ['XDG_SESSION_TYPE'] = 'wayland'
        self.assertTrue(wlclip.session_is_wayland())

    def test_wayland_display_alone_counts(self):
        # 有的桌面（比如某些登录管理器）不设 XDG_SESSION_TYPE
        os.environ['WAYLAND_DISPLAY'] = 'wayland-0'
        self.assertTrue(wlclip.session_is_wayland())

    def test_no_wl_copy_in_x11(self):
        os.environ['XDG_SESSION_TYPE'] = 'x11'
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'):
            self.assertIsNone(wlclip.wl_copy())
            self.assertFalse(wlclip.available())

    def test_missing_wl_copy(self):
        os.environ['XDG_SESSION_TYPE'] = 'wayland'
        with mock.patch.object(wlclip.shutil, 'which', return_value=None):
            self.assertFalse(wlclip.copy_files(['/a.docx']))

    def test_probe_cached(self):
        os.environ['XDG_SESSION_TYPE'] = 'wayland'
        with mock.patch.object(wlclip.shutil, 'which',
                               return_value='/usr/bin/wl-copy') as which:
            wlclip.wl_copy()
            wlclip.wl_copy()
            self.assertEqual(which.call_count, 1)
            # reset 之后才会重新探测（测试里换环境时用）
            wlclip.reset()
            wlclip.wl_copy()
            self.assertEqual(which.call_count, 2)


class CopyTest(unittest.TestCase):
    def setUp(self):
        wlclip.reset()
        os.environ['XDG_SESSION_TYPE'] = 'wayland'
        self.addCleanup(wlclip.reset)

    def _fake_popen(self, returncode=0):
        proc = mock.Mock()
        proc.returncode = returncode
        proc.communicate.return_value = (b'', b'')
        return proc

    def test_argv_and_payload(self):
        proc = self._fake_popen()
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'), \
                mock.patch.object(wlclip.subprocess, 'Popen', return_value=proc) as popen, \
                mock.patch.object(wlclip.core, 'state_dir', return_value='/tmp'):
            self.assertTrue(wlclip.copy_files(['/a.docx']))
        argv = popen.call_args[0][0]
        self.assertEqual(argv, ['/usr/bin/wl-copy', '--type', 'text/uri-list'])
        self.assertEqual(proc.communicate.call_args[0][0],
                         b'file:///a.docx\r\n')

    def test_does_not_use_paste_once(self):
        # --paste-once 会让第二次粘贴拿到空内容，用户会以为坏了
        proc = self._fake_popen()
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'), \
                mock.patch.object(wlclip.subprocess, 'Popen', return_value=proc) as popen, \
                mock.patch.object(wlclip.core, 'state_dir', return_value='/tmp'):
            wlclip.copy_files(['/a.docx'])
        self.assertNotIn('--paste-once', popen.call_args[0][0])

    def test_nonzero_exit_reported_as_failure(self):
        proc = self._fake_popen(returncode=1)
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'), \
                mock.patch.object(wlclip.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(wlclip.core, 'state_dir', return_value='/tmp'):
            self.assertFalse(wlclip.copy_files(['/a.docx']))

    def test_timeout_kills_and_returns_false(self):
        import subprocess as sp
        proc = mock.Mock()
        proc.communicate.side_effect = sp.TimeoutExpired(cmd='wl-copy', timeout=3)
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'), \
                mock.patch.object(wlclip.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(wlclip.core, 'state_dir', return_value='/tmp'):
            self.assertFalse(wlclip.copy_files(['/a.docx']))
        proc.kill.assert_called_once()

    def test_empty_paths_not_called(self):
        with mock.patch.object(wlclip.shutil, 'which', return_value='/usr/bin/wl-copy'), \
                mock.patch.object(wlclip.subprocess, 'Popen') as popen:
            self.assertFalse(wlclip.copy_files([]))
        popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
