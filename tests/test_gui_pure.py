# -*- coding: utf-8 -*-
"""gui 里的纯函数：来源判断、类型归类、时间分桶、大小格式化。

这些函数决定列表里每一行长什么样，改错了界面会「看起来正常但内容不对」，
比崩溃更难发现，所以逐个钉住。不需要显示器，也不创建任何窗口。
"""
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault('GDK_BACKEND', 'x11')

try:
    from wpsclip import gui
    _IMPORT_ERR = None
except Exception as _ex:      # 没装 PyGObject 的环境跳过，而不是报错
    gui = None
    _IMPORT_ERR = _ex

_need_gi = unittest.skipIf(gui is None, '需要 PyGObject（gi）')


@_need_gi
class SourceOfTest(unittest.TestCase):
    def test_cloud(self):
        self.assertEqual(gui.source_of('/home/u/WPS Cloud Files/a.docx'), '云端')
        self.assertEqual(gui.source_of('/home/u/wpscloud/a.docx'), '云端')
        self.assertEqual(gui.source_of('/home/u/云文档/a.docx'), '云端')

    def test_wechat(self):
        self.assertEqual(gui.source_of('/home/u/xwechat_files/a.pdf'), '微信')
        self.assertEqual(gui.source_of('/home/u/WeChat Files/a.pdf'), '微信')
        self.assertEqual(gui.source_of('/home/u/微信/a.pdf'), '微信')

    def test_local_default(self):
        self.assertEqual(gui.source_of('/home/u/Desktop/a.docx'), '本地')
        self.assertEqual(gui.source_of('/tmp/a.xlsx'), '本地')

    def test_cloud_wins_over_wechat(self):
        # 路径里同时出现两者时，云端的判断在前，结果应为云端
        self.assertEqual(
            gui.source_of('/home/u/WPS Cloud Files/wechat/a.docx'), '云端')


@_need_gi
class TypeLabelTest(unittest.TestCase):
    def test_categories(self):
        cases = {
            '.doc': '文档', '.docx': '文档', '.wps': '文档', '.rtf': '文档',
            '.xls': '表格', '.xlsx': '表格', '.et': '表格', '.csv': '表格',
            '.ppt': '演示', '.pptx': '演示', '.dps': '演示',
            '.pdf': 'PDF', '.ofd': 'PDF',
            '.txt': '文本', '.md': '文本',
        }
        for ext, want in cases.items():
            self.assertEqual(gui.type_label('/a' + ext), want, ext)

    def test_case_insensitive(self):
        self.assertEqual(gui.type_label('/A.DOCX'), '文档')

    def test_unknown_and_no_ext(self):
        self.assertEqual(gui.type_label('/a.xyz'), '其他')
        self.assertEqual(gui.type_label('/noext'), '其他')


@_need_gi
class TypeIconTest(unittest.TestCase):
    def test_icons(self):
        cases = {
            '.docx': 'x-office-document',
            '.xlsx': 'x-office-spreadsheet',
            '.pptx': 'x-office-presentation',
            '.pdf': 'application-pdf',
            '.png': 'image-x-generic',
            '.txt': 'text-x-generic',
        }
        for ext, want in cases.items():
            self.assertEqual(gui.type_icon('/a' + ext), want, ext)

    def test_fallback_icon_is_never_empty(self):
        for name in ('/a.xyz', '/noext', '/.hidden'):
            self.assertTrue(gui.type_icon(name))


@_need_gi
class BucketOfTest(unittest.TestCase):
    DAY = 86400

    def idx(self, age_seconds):
        return gui.bucket_of(time.time() - age_seconds)[0]

    def test_zero_mtime_is_oldest(self):
        self.assertEqual(gui.bucket_of(0)[0], 5)
        self.assertEqual(gui.bucket_of(0)[1], '更早')

    def test_today(self):
        self.assertEqual(self.idx(0), 0)
        self.assertEqual(self.idx(3600), 0)

    def test_yesterday(self):
        self.assertEqual(self.idx(self.DAY), 1)

    def test_within_3_days(self):
        self.assertEqual(self.idx(2 * self.DAY), 2)
        self.assertEqual(self.idx(3 * self.DAY), 2)

    def test_within_7_days(self):
        self.assertEqual(self.idx(4 * self.DAY), 3)
        self.assertEqual(self.idx(7 * self.DAY), 3)

    def test_within_30_days(self):
        self.assertEqual(self.idx(8 * self.DAY), 4)
        self.assertEqual(self.idx(30 * self.DAY), 4)

    def test_older(self):
        self.assertEqual(self.idx(31 * self.DAY), 5)
        self.assertEqual(self.idx(400 * self.DAY), 5)

    def test_index_matches_label_table(self):
        for age in (0, self.DAY, 3 * self.DAY, 7 * self.DAY,
                    30 * self.DAY, 400 * self.DAY):
            idx, label = gui.bucket_of(time.time() - age)
            self.assertEqual(label, gui._BUCKETS[idx])

    def test_buckets_are_monotonic(self):
        # 时间越久，桶序号不能变小（顺序错了分组会乱）
        prev = -1
        for age in (0, self.DAY, 3 * self.DAY, 7 * self.DAY,
                    30 * self.DAY, 400 * self.DAY):
            cur = self.idx(age)
            self.assertGreaterEqual(cur, prev)
            prev = cur


@_need_gi
class HumanSizeTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(gui.human_size(0), '0 B')
        self.assertEqual(gui.human_size(512), '512 B')
        self.assertEqual(gui.human_size(1023), '1023 B')

    def test_kb_mb_gb(self):
        self.assertEqual(gui.human_size(1024), '1.0 KB')
        self.assertEqual(gui.human_size(1536), '1.5 KB')
        self.assertEqual(gui.human_size(1024 ** 2), '1.0 MB')
        self.assertEqual(gui.human_size(int(1.5 * 1024 ** 2)), '1.5 MB')
        self.assertEqual(gui.human_size(1024 ** 3), '1.0 GB')

    def test_beyond_gb_does_not_crash(self):
        self.assertEqual(gui.human_size(1024 ** 4), '1024.0 GB')



class ClampWindowPosTest(unittest.TestCase):
    """记住的窗口坐标要能安全还原：换屏、改分辨率后不能跑到屏幕外。"""

    def test_normal_position_kept(self):
        self.assertEqual(gui.clamp_window_pos(300, 200, 800, 600, 1920, 1080),
                         (300, 200))

    def test_negative_pulled_back(self):
        # 上次在左边那块副屏上，现在只剩主屏 -> 不能留在负坐标
        self.assertEqual(gui.clamp_window_pos(-500, -300, 800, 600, 1920, 1080),
                         (0, 0))

    def test_beyond_screen_pulled_back(self):
        x, y = gui.clamp_window_pos(3000, 2000, 800, 600, 1920, 1080)
        self.assertLess(x, 1920)
        self.assertLess(y, 1080)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_keeps_a_grab_handle_visible(self):
        # 至少留 120px 在屏内，鼠标够得着拖回来
        x, y = gui.clamp_window_pos(1910, 1070, 800, 600, 1920, 1080)
        self.assertLessEqual(x, 1920 - 120)
        self.assertLessEqual(y, 1080 - 120)

    def test_garbage_returns_none(self):
        self.assertIsNone(gui.clamp_window_pos(None, None, 0, 0, 1920, 1080))


class _FakeGdkWindow(object):
    """替身：GDK 侧的窗口，只提供状态和 raise_。"""

    def __init__(self, state=0):
        self._state = state
        self.raised = 0

    def get_state(self):
        return self._state

    def raise_(self):
        self.raised += 1


class _FakeWindow(object):
    """替身：Gtk.Window，只记下被调过哪些动作。"""

    def __init__(self, gdk_state=0):
        self.calls = []
        self.gdk = _FakeGdkWindow(gdk_state)

    def deiconify(self):
        self.calls.append('deiconify')

    def present_with_time(self, _ts):
        self.calls.append('present')

    def get_window(self):
        return self.gdk


class _FakeApp(object):
    """只带唤回逻辑要用的成员，避免真去建窗口。"""

    def __init__(self, wm_state, gdk_state=0):
        self.win = _FakeWindow(gdk_state)
        self._wm = wm_state
        self.activated = 0
        self.remapped = 0
        # 这两条来自 gui.App，替身要自己带上
        self.RAISE_STEPS = gui.App.RAISE_STEPS if gui else 10
        self.RAISE_REMAP_STEP = gui.App.RAISE_REMAP_STEP if gui else 5
        self.RAISE_INTERVAL = 70

    def _wm_state(self):
        return self._wm

    def _x11_activate(self):
        self.activated += 1
        return True

    def _remap_window(self):
        self.remapped += 1


if gui is not None:
    # 直接把被测方法挂到替身上，测的就是 gui.App 里那段逻辑本身
    _FakeApp._kick_raise = gui.App._kick_raise
    _FakeApp._is_iconified = gui.App._is_iconified


@_need_gi
class WakeUpTest(unittest.TestCase):
    """最小化到任务栏后，点托盘要能把窗口捞回来。

    这里钉的是两个容易回退的点：判断「是否最小化」不能看 GDK 的缓存状态
    （自己调过 deiconify 就被抹掉了），以及重试要按拍进行、到点兜底重挂。
    """

    ICONIC = gui.ICCCM_ICONIC

    def test_wm_state_wins_over_gdk(self):
        # 窗管说还在任务栏里，即使 GDK 缓存说没有，也必须算最小化
        app = _FakeApp(wm_state=self.ICONIC, gdk_state=0)
        self.assertTrue(app._is_iconified())
        # 反过来：窗管已经恢复了，GDK 残留的图标化标记不能让我们继续抢窗口
        app = _FakeApp(wm_state=1, gdk_state=gui.Gdk.WindowState.ICONIFIED)
        self.assertFalse(app._is_iconified())

    def test_gdk_fallback_without_wm_state(self):
        app = _FakeApp(wm_state=None, gdk_state=gui.Gdk.WindowState.ICONIFIED)
        self.assertTrue(app._is_iconified())
        app = _FakeApp(wm_state=None, gdk_state=0)
        self.assertFalse(app._is_iconified())

    def _kick(self, app, step, iconic):
        """跑一拍唤回，返回被安排到下一拍的参数列表。"""
        scheduled = []
        orig = gui.GLib.timeout_add
        gui.GLib.timeout_add = lambda ms, cb, *a: (scheduled.append((ms, a)), 0)[1]
        try:
            app._kick_raise(step, iconic)
        finally:
            gui.GLib.timeout_add = orig
        return scheduled

    def test_retries_while_still_iconic(self):
        app = _FakeApp(wm_state=self.ICONIC)
        scheduled = self._kick(app, 0, True)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][1][0], 1)          # 下一拍是 step=1
        self.assertEqual(app.win.calls, ['deiconify', 'present'])
        self.assertEqual(app.activated, 1)               # 走了一条 EWMH 激活
        self.assertEqual(app.remapped, 0)

    def test_stops_once_window_is_back(self):
        # 重试拍会重新问窗管：窗口已经出来了就收手，不再抢
        app = _FakeApp(wm_state=1)
        scheduled = self._kick(app, 1, True)
        self.assertEqual(scheduled, [])
        self.assertEqual(app.activated, 0)               # 也不再发激活请求
        self.assertEqual(app.win.calls, ['present'])     # 只抬一次

    def test_first_step_trusts_caller_state(self):
        # 第一拍用的是 show() 之前抓到的状态：那时 GDK 还没被自己的
        # deiconify 抹掉标记，比事后再问更准，不能在这一拍重新判定
        app = _FakeApp(wm_state=1)
        scheduled = self._kick(app, 0, True)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(app.activated, 1)

    def test_remaps_at_the_designed_step(self):
        app = _FakeApp(wm_state=self.ICONIC)
        self._kick(app, gui.App.RAISE_REMAP_STEP, True)
        self.assertEqual(app.remapped, 1)

    def test_no_remap_before_that_step(self):
        app = _FakeApp(wm_state=self.ICONIC)
        self._kick(app, gui.App.RAISE_REMAP_STEP - 1, True)
        self.assertEqual(app.remapped, 0)

    def test_gives_up_at_the_last_step(self):
        app = _FakeApp(wm_state=self.ICONIC)
        scheduled = self._kick(app, gui.App.RAISE_STEPS - 1, True)
        self.assertEqual(scheduled, [])                  # 不再排下一拍，别死循环
        self.assertEqual(app.win.calls, ['deiconify', 'present'])



if __name__ == '__main__':
    if _IMPORT_ERR is not None:
        print('跳过：无法导入 gui（%r）' % (_IMPORT_ERR,))
    unittest.main()
