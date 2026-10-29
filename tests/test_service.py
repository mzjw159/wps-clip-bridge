# -*- coding: utf-8 -*-
"""复制链路的编排逻辑：路径整理、请求发布、等待确认、异步回调。

这条链路原来在 GUI 主线程里同步跑，最长会卡三秒；现在拆成可单独测的
几段，并且能在不起 X、不起后台进程的情况下验证组合行为。
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wpsclip import core, service  # noqa: E402

_ENV_KEYS = ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR')


class _TmpEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpssvc_')
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.tmp, 'config')
        os.environ['XDG_STATE_HOME'] = os.path.join(self.tmp, 'state')
        os.environ['XDG_RUNTIME_DIR'] = os.path.join(self.tmp, 'run')
        os.makedirs(os.environ['XDG_RUNTIME_DIR'], exist_ok=True)
        self.files = []
        for name in ('a.docx', 'b.xlsx'):
            p = os.path.join(self.tmp, name)
            with open(p, 'w', encoding='utf-8') as f:
                f.write('x')
            self.files.append(p)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class PrepareTest(_TmpEnv):
    def test_empty_gives_error(self):
        paths, err = service.prepare([])
        self.assertEqual(paths, [])
        self.assertTrue(err)

    def test_missing_files_are_dropped(self):
        paths, err = service.prepare([self.files[0], '/no/such/file.pdf'])
        self.assertEqual(paths, [self.files[0]])
        self.assertEqual(err, '')

    def test_all_missing_is_an_error(self):
        paths, err = service.prepare(['/no/such/file.pdf', ''])
        self.assertEqual(paths, [])
        self.assertIn('路径不存在', err)

    def test_dedupes_and_keeps_order(self):
        p = self.files[0]
        paths, _ = service.prepare([p, self.files[1], p])
        self.assertEqual(paths, [p, self.files[1]])

    def test_relative_and_tilde(self):
        old = os.getcwd()
        os.chdir(self.tmp)
        try:
            paths, _ = service.prepare(['a.docx'])
        finally:
            os.chdir(old)
        self.assertEqual(paths, [os.path.join(self.tmp, 'a.docx')])
        os.environ['HOME'] = self.tmp
        try:
            paths, _ = service.prepare(['~/b.xlsx'])
        finally:
            os.environ.pop('HOME', None)
        self.assertEqual(paths, [self.files[1]])

    def test_directory_is_not_a_file(self):
        paths, err = service.prepare([self.tmp])
        self.assertEqual(paths, [])
        self.assertTrue(err)


class PublishTest(_TmpEnv):
    def test_writes_request_file_atomically(self):
        marker = service.publish(self.files)
        with open(core.req_path(), encoding='utf-8') as f:
            self.assertEqual(f.read(), '\n'.join(self.files) + '\n')
        # 没有残留的临时文件
        leftovers = [n for n in os.listdir(os.path.dirname(core.req_path()))
                     if '.tmp' in n]
        self.assertEqual(leftovers, [])
        self.assertIsNone(marker)          # 第一次发布前还没有 published

    def test_marker_tracks_published(self):
        pub = core.runtime_file('published')
        core.touch(pub)
        first = service.publish(self.files)
        self.assertIsNotNone(first)
        time.sleep(0.02)
        core.touch(pub)
        second = service.publish(self.files)
        self.assertNotEqual(first, second)


class WaitPublishedTest(_TmpEnv):
    def test_times_out_when_bridge_silent(self):
        pub = core.runtime_file('published')
        core.touch(pub)
        marker = core.mtime(pub)
        t0 = time.time()
        self.assertFalse(service.wait_published(marker, timeout=0.2))
        self.assertLess(time.time() - t0, 1.5)

    def test_true_when_marker_changes(self):
        pub = core.runtime_file('published')
        core.touch(pub)
        marker = core.mtime(pub)
        time.sleep(0.02)
        core.touch(pub)                    # 模拟 bridge 处理完这次请求
        self.assertTrue(service.wait_published(marker, timeout=0.5))

    def test_missing_published_never_matches_missing_marker(self):
        # published 还没出现过（marker=None）时不能误判成「已完成」
        self.assertFalse(service.wait_published(None, timeout=0.2))


class RequestCopyTest(_TmpEnv):
    def setUp(self):
        super().setUp()
        self._orig = (service.ensure_bridge, service.bridge_running,
                      service.wait_published)

    def tearDown(self):
        (service.ensure_bridge, service.bridge_running,
         service.wait_published) = self._orig
        super().tearDown()

    def test_no_files_short_circuits(self):
        ok, msg = service.request_copy(['/no/such/file'])
        self.assertFalse(ok)
        self.assertIn('没有可复制的文件', msg)

    def test_bridge_failure_is_reported(self):
        service.ensure_bridge = lambda wait=0: False
        service.bridge_running = lambda: False
        ok, msg = service.request_copy(self.files)
        self.assertFalse(ok)
        self.assertIn('后台服务未能启动', msg)

    def test_success_writes_request_and_reports_stages(self):
        stages = []
        service.ensure_bridge = lambda wait=0: True
        service.bridge_running = lambda: False      # 走「正在启动后台服务」分支
        service.wait_published = lambda marker, timeout=3.0: True
        ok, msg = service.request_copy(self.files, on_stage=stages.append)
        self.assertTrue(ok)
        self.assertEqual(msg, '')
        self.assertTrue(any('启动后台' in s for s in stages))
        with open(core.req_path(), encoding='utf-8') as f:
            self.assertEqual(f.read().split(), self.files)

    def test_stage_callback_errors_do_not_break_copy(self):
        service.ensure_bridge = lambda wait=0: True
        service.bridge_running = lambda: True
        service.wait_published = lambda marker, timeout=3.0: True

        def boom(_text):
            raise RuntimeError('界面已经关了')

        ok, msg = service.request_copy(self.files, on_stage=boom)
        self.assertTrue(ok, msg)


class CopyAsyncTest(_TmpEnv):
    def setUp(self):
        super().setUp()
        self._orig = service.request_copy
        self.requests = []

    def tearDown(self):
        service.request_copy = self._orig
        super().tearDown()

    def _wait(self, done, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline and not done:
            time.sleep(0.01)
        self.assertTrue(done, '回调没有在 %s 秒内触发' % timeout)

    def test_normalises_then_calls_back_in_thread(self):
        result = {}

        def fake_copy(paths, timeout=3.0, on_stage=None):
            self.requests.append(paths)
            return True, ''

        service.request_copy = fake_copy

        def done(ok, msg, paths):
            result.update(ok=ok, paths=paths, thread=threading.current_thread())

        service.copy_async([self.files[0], '/no/such'], done)
        self._wait(result)
        self.assertTrue(result['ok'])
        self.assertEqual(result['paths'], [self.files[0]])
        self.assertEqual(self.requests, [[self.files[0]]])
        self.assertIsNot(result['thread'], threading.main_thread())

    def test_invalid_paths_report_without_spawning(self):
        calls = []
        service.request_copy = lambda *a, **kw: calls.append(a) or (True, '')
        result = {}

        def done(ok, msg, paths):
            result.update(ok=ok, msg=msg, paths=paths)

        t = service.copy_async(['/no/such/file'], done)
        self.assertIsNone(t)
        self.assertFalse(result['ok'])
        self.assertEqual(result['paths'], [])
        self.assertEqual(calls, [])

    def test_exception_becomes_failure(self):
        def boom(paths, timeout=3.0, on_stage=None):
            raise OSError('X 连不上')

        service.request_copy = boom
        result = {}
        service.copy_async(self.files, lambda ok, msg, paths: result.update(
            ok=ok, msg=msg))
        self._wait(result)
        self.assertFalse(result['ok'])
        self.assertIn('X 连不上', result['msg'])

if __name__ == '__main__':
    unittest.main()
