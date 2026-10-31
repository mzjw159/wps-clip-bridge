# -*- coding: utf-8 -*-
"""热键复制这条路径的桌面通知。

这里守的是一个实际踩过的 bug：`copy_latest()` 复制完只写日志、从不发通知，
而热键路径既不弹窗口也没有状态栏 —— 用户按完 Ctrl+Shift+C 完全不知道
成没成，只能去翻 log。设置里明明勾了「复制成功后发送桌面通知」。

四个分支都要有反馈：
- 成功且开了通知  -> 「已复制 xxx」
- 成功但关了通知  -> 一声不响（这是用户的选择）
- 复制失败        -> 必须说，否则用户以为程序死了
- 没有最近文档    -> 必须说，否则用户以为热键没生效
"""
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wpsclip import core, hotkey, recent, service  # noqa: E402

_ENV_KEYS = ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR')


class CopyLatestNotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpshotkey_')
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.tmp, 'config')
        os.environ['XDG_STATE_HOME'] = os.path.join(self.tmp, 'state')
        os.environ['XDG_RUNTIME_DIR'] = os.path.join(self.tmp, 'run')
        os.makedirs(os.environ['XDG_RUNTIME_DIR'], exist_ok=True)
        os.chmod(os.environ['XDG_RUNTIME_DIR'], 0o700)
        self._reset_cache()

        self.calls = []
        self.rows = ['/tmp/季度报告.docx']
        self.ok = True
        self.msg = ''
        self.boom = None

        self._orig = (recent.parse_recent, service.request_copy, service.notify)
        recent.parse_recent = lambda limit=1: self.rows
        service.request_copy = self._fake_copy
        service.notify = lambda summary, body='': self.calls.append((summary, body))

    def tearDown(self):
        recent.parse_recent, service.request_copy, service.notify = self._orig
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _reset_cache():
        core._config = None
        core._config_mtime = None

    def _fake_copy(self, paths, timeout=None, on_stage=None):
        if self.boom is not None:
            raise self.boom
        return self.ok, self.msg

    # ------------------------------------------------------------------
    def test_notifies_on_success(self):
        self.assertTrue(hotkey.copy_latest())
        self.assertEqual(len(self.calls), 1, '复制成功时应该发一条通知')
        summary, body = self.calls[0]
        self.assertIn('季度报告.docx', summary)

    def test_silent_when_user_turned_off(self):
        core.save_conf({('ui', 'notify_on_copy'): 'false'})
        self._reset_cache()
        self.assertTrue(hotkey.copy_latest())
        self.assertEqual(self.calls, [], '关掉通知后不该再打扰用户')

    def test_notifies_on_failure_even_if_notify_off(self):
        core.save_conf({('ui', 'notify_on_copy'): 'false'})
        self._reset_cache()
        self.ok, self.msg = False, '后台服务未能启动'
        self.assertFalse(hotkey.copy_latest())
        self.assertEqual(len(self.calls), 1, '失败必须说出来，哪怕用户关了成功通知')
        self.assertIn('后台服务未能启动', self.calls[0][1])

    def test_notifies_when_no_recent_document(self):
        self.rows = []
        self.assertFalse(hotkey.copy_latest())
        self.assertEqual(len(self.calls), 1)
        self.assertIn('没有', self.calls[0][0])

    def test_notifies_when_parsing_raises(self):
        def boom(limit=1):
            raise OSError('配置文件读不了')
        recent.parse_recent = boom
        self.assertFalse(hotkey.copy_latest())
        self.assertEqual(len(self.calls), 1)

    def test_copy_gets_exactly_one_path(self):
        seen = {}

        def spy(paths, timeout=None, on_stage=None):
            seen['paths'] = paths
            return True, ''
        service.request_copy = spy
        hotkey.copy_latest()
        self.assertEqual(seen['paths'], ['/tmp/季度报告.docx'],
                         '热键只复制最近一个文档')


class DefaultHotkeyTest(unittest.TestCase):
    def test_copy_combo_default(self):
        self.assertEqual(core.DEFAULTS['hotkey']['copy_combo'], '<Control><Shift>c')

    def test_default_is_parsable(self):
        parsed = hotkey.parse_combo(core.DEFAULTS['hotkey']['copy_combo'])
        self.assertIsNotNone(parsed)
        mask, key = parsed
        self.assertEqual(key, 'c')
        self.assertTrue(mask)


class ComboConflictsGuardTest(unittest.TestCase):
    """占用探测在「问不出答案」时必须返回 None，不能报 0 也不能抛。

    设置窗口拿这个返回值决定要不要提示「已被其它程序占用」，误报会把用户
    吓得换掉一个好用的组合键。
    """

    def test_empty_combo(self):
        self.assertIsNone(hotkey.combo_conflicts(''))

    def test_modifiers_only(self):
        # 只有修饰键、没有主键：不是组合键
        self.assertIsNone(hotkey.combo_conflicts('<Control><Alt>'))

    def test_bare_key(self):
        # 没有修饰键的组合设置里也不允许，别去真抓一个裸键
        self.assertIsNone(hotkey.combo_conflicts('c'))


if __name__ == '__main__':
    unittest.main()
