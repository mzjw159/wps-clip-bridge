# -*- coding: utf-8 -*-
"""config.ini 读写、默认值回退、mtime 热重载、开机自启开关。

这些逻辑一旦错了，表现是「设置保存了但没生效」，而日志里什么都看不到，
所以值得钉死。每个用例都在临时目录里跑，绝不碰真实配置。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wpsclip import core  # noqa: E402

_ENV_KEYS = ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR')


class _TmpHome(unittest.TestCase):
    """把 XDG 目录整体搬到临时目录，杜绝污染真实环境。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpscore_')
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.tmp, 'config')
        os.environ['XDG_STATE_HOME'] = os.path.join(self.tmp, 'state')
        os.environ['XDG_RUNTIME_DIR'] = os.path.join(self.tmp, 'run')
        os.makedirs(os.environ['XDG_RUNTIME_DIR'], exist_ok=True)
        os.chmod(os.environ['XDG_RUNTIME_DIR'], 0o700)
        self._reset_cache()
        # 系统自启项指向一个不存在的路径，让 set_autostart 的行为可预测
        self._sys_autostart = core.SYSTEM_AUTOSTART
        core.SYSTEM_AUTOSTART = os.path.join(self.tmp, 'no-such-system.desktop')

    def tearDown(self):
        core.SYSTEM_AUTOSTART = self._sys_autostart
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


class DefaultsTest(_TmpHome):
    def test_no_file_uses_defaults(self):
        self.assertEqual(core.conf('ui', 'theme', 'system'), 'system')
        self.assertEqual(core.conf('hotkey', 'combo', ''), '<Control><Alt>c')
        self.assertEqual(core.conf('hotkey', 'copy_combo', ''), '<Control><Shift>c')
        self.assertTrue(core.conf_bool('hotkey', 'enabled', False))

    def test_unknown_section_falls_back(self):
        self.assertEqual(core.conf('nope', 'nope', 'fallback'), 'fallback')

    def test_conf_bool_truthy(self):
        for v in ('1', 'true', 'TRUE', ' true ', 'yes', 'on'):
            core.save_conf({('ui', 'probe'): v})
            self.assertTrue(core.conf_bool('ui', 'probe', False), 'value=%r' % v)

    def test_conf_bool_falsy(self):
        for v in ('0', 'false', 'no', 'off', ''):
            core.save_conf({('ui', 'probe'): v})
            self.assertFalse(core.conf_bool('ui', 'probe', True), 'value=%r' % v)

    def test_conf_int_bad_value_falls_back(self):
        core.save_conf({('ui', 'limit'): 'eighty'})
        self.assertEqual(core.conf_int('ui', 'limit', 80), 80)

    def test_conf_int_good_value(self):
        core.save_conf({('ui', 'limit'): '120'})
        self.assertEqual(core.conf_int('ui', 'limit', 80), 120)

    def test_conf_float_bad_value_falls_back(self):
        core.save_conf({('bridge', 'poll'): 'fast'})
        self.assertAlmostEqual(core.conf_float('bridge', 'poll', 0.3), 0.3)


class SaveTest(_TmpHome):
    def test_save_then_read_back(self):
        core.save_conf({('ui', 'theme'): 'dark'})
        self.assertEqual(core.conf('ui', 'theme', 'system'), 'dark')
        with open(core.config_file(), encoding='utf-8') as f:
            self.assertIn('dark', f.read())

    def test_save_creates_section(self):
        core.save_conf({('brand-new', 'key'): 'v'})
        self.assertEqual(core.conf('brand-new', 'key', ''), 'v')

    def test_save_keeps_other_keys(self):
        core.save_conf({('ui', 'theme'): 'dark'})
        core.save_conf({('ui', 'limit'): '40'})
        self.assertEqual(core.conf('ui', 'theme', ''), 'dark')
        self.assertEqual(core.conf('ui', 'limit', ''), '40')

    def test_save_atomic_no_tmp_left(self):
        core.save_conf({('ui', 'theme'): 'light'})
        leftovers = [n for n in os.listdir(os.path.dirname(core.config_file()))
                     if '.tmp' in n]
        self.assertEqual(leftovers, [])

    def test_hot_reload_when_file_changes(self):
        core.save_conf({('ui', 'theme'): 'light'})
        self.assertEqual(core.conf('ui', 'theme', ''), 'light')
        # 绕过 save_conf 直接改文件，模拟外部编辑器修改
        path = core.config_file()
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[ui]\ntheme = dark\n')
        future = time.time() + 5
        os.utime(path, (future, future))
        self.assertEqual(core.conf('ui', 'theme', ''), 'dark')


class AutostartTest(_TmpHome):
    def test_off_writes_hidden_override(self):
        self.assertFalse(core.autostart_enabled())
        self.assertFalse(core.set_autostart(False))
        self.assertTrue(os.path.exists(core.autostart_file()))
        with open(core.autostart_file(), encoding='utf-8') as f:
            self.assertIn('Hidden=true', f.read())

    def test_on_removes_override(self):
        core.set_autostart(False)
        self.assertFalse(core.autostart_enabled())
        self.assertTrue(core.set_autostart(True))
        with open(core.autostart_file(), encoding='utf-8') as f:
            self.assertNotIn('Hidden=true', f.read())

    def test_hidden_with_spaces_still_parsed(self):
        os.makedirs(os.path.dirname(core.autostart_file()), exist_ok=True)
        with open(core.autostart_file(), 'w', encoding='utf-8') as f:
            f.write('[Desktop Entry]\nHidden = true\n')
        self.assertFalse(core.autostart_enabled())


class PathTest(_TmpHome):
    def test_paths_live_under_xdg(self):
        self.assertTrue(core.config_home().startswith(self.tmp))
        self.assertTrue(core.state_dir().startswith(self.tmp))
        self.assertTrue(core.runtime_dir().startswith(self.tmp))

    def test_named_files(self):
        self.assertTrue(core.req_path().endswith('copy-request'))
        self.assertTrue(core.gui_wake_path().endswith('gui-wake'))
        self.assertTrue(core.lock_path('bridge').endswith('bridge.lock'))


class StaleRequestTest(_TmpHome):
    """bridge 启动时要不要执行现成的请求文件。

    判定错了有两个方向的症状：太宽松 -> 一登录就把上次复制的文件又塞回
    剪贴板；太严格 -> 冷启动时先写下的请求被忽略，第一次复制「没反应」。
    """

    def test_missing_file_is_not_stale(self):
        self.assertFalse(core.request_is_stale(None))

    def test_fresh_request_is_executed(self):
        now = 1_000_000.0
        self.assertFalse(core.request_is_stale(now - 2, now=now))
        self.assertFalse(core.request_is_stale(now, now=now))

    def test_old_request_is_ignored(self):
        now = 1_000_000.0
        self.assertTrue(core.request_is_stale(now - 61, now=now))
        self.assertTrue(core.request_is_stale(now - 86400, now=now))

    def test_boundary_uses_default_60s(self):
        now = 1_000_000.0
        self.assertFalse(core.request_is_stale(now - 59.9, now=now))
        self.assertTrue(core.request_is_stale(now - 60.1, now=now))

    def test_custom_window(self):
        now = 1_000_000.0
        self.assertTrue(core.request_is_stale(now - 6, now=now, stale=5))
        self.assertFalse(core.request_is_stale(now - 4, now=now, stale=5))

    def test_real_file_mtime(self):
        p = os.path.join(self.tmp, 'copy-request')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('/tmp/a\n')
        self.assertFalse(core.request_is_stale(core.mtime(p)))
        self.assertTrue(core.request_is_stale(core.mtime(p), now=time.time() + 3600))


if __name__ == '__main__':
    unittest.main()
