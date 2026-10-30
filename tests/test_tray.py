# -*- coding: utf-8 -*-
"""托盘：菜单定义、后端回退顺序、SNI 的图标编码与菜单事件。

这几处都是「看起来没问题、实际却不工作」的高危区：
* IconPixmap 的字节序错一位，面板上就是一个花屏小方块；
* dbusmenu 的 id 对不上，右键菜单点了没反应；
* 后端回退顺序写错，明明能用原生 SNI 却退回弹菜单的 AppIndicator。

全部不需要显示器、不需要会话总线。
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault('GDK_BACKEND', 'x11')

try:
    from wpsclip import sni, tray
    _IMPORT_ERR = None
except Exception as _ex:      # 没装 PyGObject 的环境跳过
    sni = tray = None
    _IMPORT_ERR = _ex

_need_gi = unittest.skipIf(sni is None, '需要 PyGObject（gi）')


class _FakeInvocation(object):
    """Gio.DBusMethodInvocation 的最小替身。"""

    def __init__(self):
        self.value = 'unset'
        self.error = None

    def return_value(self, v):
        self.value = v

    def return_dbus_error(self, name, msg=''):
        self.error = (name, msg)


@_need_gi
class ArgbTest(unittest.TestCase):
    def test_rgba_to_argb(self):
        try:
            from gi.repository import GdkPixbuf
        except Exception:
            self.skipTest('需要 GdkPixbuf')
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 1)
        # 第一个像素 R=0x11 G=0x22 B=0x33 A=0x44，第二个反过来
        data = bytearray(pb.get_pixels())
        data[0:4] = b'\x11\x22\x33\x44'
        data[4:8] = b'\xaa\xbb\xcc\xdd'
        pb = GdkPixbuf.Pixbuf.new_from_bytes(
            sni.GLib.Bytes.new(bytes(data)),
            GdkPixbuf.Colorspace.RGB, True, 8, 2, 1, pb.get_rowstride())
        out = sni._argb32(pb)
        self.assertEqual(len(out), 8)
        self.assertEqual(tuple(out[0:4]), (0x44, 0x11, 0x22, 0x33))
        self.assertEqual(tuple(out[4:8]), (0xdd, 0xaa, 0xbb, 0xcc))

    def test_pixmaps_have_sizes(self):
        pbs = sni.icon_pixmaps((16,))
        if not pbs:
            self.skipTest('当前环境取不到图标')
        w, h, data = pbs[0]
        self.assertEqual((w, h), (16, 16))
        self.assertEqual(len(data), 16 * 16 * 4)

    def test_fallback_icon_when_files_missing(self):
        """一台机器上找不到图标文件时，也得给面板一个能认的图标。

        退回空列表 = 面板画系统默认齿轮，用户会以为托盘上挂的是别的程序。
        """
        from wpsclip import gui

        def boom(sizes):
            return []

        old = gui._icon_pixbufs
        gui._icon_pixbufs = boom
        try:
            pbs = sni.icon_pixmaps((16,))
        finally:
            gui._icon_pixbufs = old
        if not pbs:
            self.skipTest('当前环境画不出兜底图标')
        w, h, data = pbs[0]
        self.assertEqual((w, h), (16, 16))
        self.assertEqual(len(data), 16 * 16 * 4)
        # 兜底是紫底：抽中间一行，必须有不透明的紫色像素
        alpha = [data[i] for i in range(0, len(data), 4)]
        self.assertTrue(any(a > 0x80 for a in alpha))


@_need_gi
class MenuBuildTest(unittest.TestCase):
    def _item(self, calls=None):
        calls = calls if calls is not None else []
        return sni.SniItem(
            [('显示窗口', lambda: calls.append('show')),
             ('复制最近文档', lambda: calls.append('copy')),
             None,
             ('设置…', lambda: calls.append('settings')),
             None,
             ('退出', lambda: calls.append('quit'))],
            on_activate=lambda: calls.append('activate')), calls

    def test_ids_and_separators(self):
        item, _ = self._item()
        self.assertEqual(sorted(item._nodes), [1, 2, 3, 4, 5, 6])
        self.assertEqual(item._nodes[3][0], {'type': 'separator'})
        self.assertIsNone(item._nodes[3][1])
        self.assertEqual(item._nodes[1][0]['label'], '显示窗口')
        self.assertEqual(len(item._root_children), 6)

    def test_root_node_is_valid_variant(self):
        item, _ = self._item()
        iid, props, children = item._root()
        self.assertEqual(iid, 0)
        self.assertIn('children-display', props)
        self.assertEqual(len(children), 6)
        self.assertEqual(children[0].get_type_string(), '(ia{sv}av)')

    def test_props_of(self):
        item, _ = self._item()
        self.assertEqual(item._props_of(0), {'children-display': 'submenu'})
        self.assertEqual(item._props_of(1)['label'], '显示窗口')
        self.assertIsNone(item._props_of(99))

    def test_click_dispatch(self):
        item, calls = self._item()
        with mock.patch.object(sni.GLib, 'idle_add', lambda f, *a: f(*a)):
            item._click(2)
            item._click(3)      # 分隔线：什么都不该发生
            item._click(99)     # 不存在的 id
        self.assertEqual(calls, ['copy'])


@_need_gi
class SniMethodsTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.item = sni.SniItem(
            [('显示窗口', lambda: self.calls.append('show'))],
            on_activate=lambda: self.calls.append('activate'),
            on_secondary=lambda: self.calls.append('middle'))

    def _call(self, method, params=None):
        inv = _FakeInvocation()
        with mock.patch.object(sni.GLib, 'idle_add', lambda f, *a: f(*a)):
            self.item._on_item_call(None, None, sni.SNI_PATH,
                                    'org.kde.StatusNotifierItem', method, params, inv)
        return inv

    def test_activate_shows_window(self):
        inv = self._call('Activate')
        self.assertEqual(self.calls, ['activate'])
        self.assertIsNone(inv.value)

    def test_secondary_activate(self):
        self._call('SecondaryActivate')
        self.assertEqual(self.calls, ['middle'])

    def test_context_menu_just_acks(self):
        inv = self._call('ContextMenu')
        self.assertIsNone(inv.value)
        self.assertEqual(self.calls, [])

    def test_item_is_menu_false(self):
        self.assertEqual(self.item._on_item_prop(
            None, None, None, None, 'ItemIsMenu').unpack(), False)

    def test_menu_path(self):
        self.assertEqual(self.item._on_item_prop(
            None, None, None, None, 'Menu').unpack(), sni.MENU_PATH)

    def test_icon_pixmap_type(self):
        v = self.item._on_item_prop(None, None, None, None, 'IconPixmap')
        self.assertEqual(v.get_type_string(), 'a(iiay)')

    def test_unknown_property(self):
        self.assertIsNone(self.item._on_item_prop(None, None, None, None, 'Nope'))

    def test_menu_event_click(self):
        inv = _FakeInvocation()
        params = sni.GLib.Variant('(isvu)', (1, 'clicked', sni.GLib.Variant('i', 0), 0))
        with mock.patch.object(sni.GLib, 'idle_add', lambda f, *a: f(*a)):
            self.item._on_menu_call(None, None, sni.MENU_PATH,
                                    'com.canonical.dbusmenu', 'Event', params, inv)
        self.assertEqual(self.calls, ['show'])
        self.assertIsNone(inv.value)

    def test_menu_get_layout(self):
        inv = _FakeInvocation()
        params = sni.GLib.Variant('(iias)', (0, -1, []))
        self.item._on_menu_call(None, None, sni.MENU_PATH,
                                'com.canonical.dbusmenu', 'GetLayout', params, inv)
        self.assertEqual(inv.value.get_type_string(), '(u(ia{sv}av))')
        rev, layout = inv.value.unpack()
        self.assertEqual(rev, 1)
        self.assertEqual(len(layout[2]), 1)

    def test_menu_about_to_show(self):
        inv = _FakeInvocation()
        params = sni.GLib.Variant('(i)', (0,))
        self.item._on_menu_call(None, None, sni.MENU_PATH,
                                'com.canonical.dbusmenu', 'AboutToShow', params, inv)
        self.assertEqual(inv.value.unpack(), (False,))


@_need_gi
class TrayRecentMenuTest(unittest.TestCase):
    """托盘菜单里的「最近 5 个」：位置、点击、刷新时机。"""

    def _tray(self, recent=(), copied=None):
        copied = copied if copied is not None else []
        t = tray.Tray(on_show=lambda: None,
                      on_copy=lambda: None,
                      on_settings=lambda: None,
                      on_quit=lambda: None,
                      on_copy_path=lambda p: copied.append(p),
                      on_recent=lambda: list(recent))
        return t, copied

    def test_recent_items_go_right_after_show(self):
        t, _ = self._tray([('a.docx', '/a.docx'), ('b.docx', '/b.docx')])
        labels = [s[0] if s else None for s in t._menu_spec()]
        self.assertEqual(labels, ['显示窗口', None, 'a.docx', 'b.docx', None,
                                  '复制最近文档', None, '设置…', None, '退出'])

    def test_no_recent_keeps_old_layout(self):
        t, _ = self._tray([])
        labels = [s[0] if s else None for s in t._menu_spec()]
        self.assertEqual(labels, ['显示窗口', '复制最近文档', None,
                                  '设置…', None, '退出'])

    def test_click_copies_that_file(self):
        t, copied = self._tray([('a.docx', '/a.docx'), ('b.docx', '/b.docx')])
        spec = t._menu_spec()
        # 第 3 项是 b.docx（0=显示窗口 1=分隔 2=a 3=b）
        spec[3][1]()
        self.assertEqual(copied, ['/b.docx'])

    def test_broken_provider_does_not_break_menu(self):
        t = tray.Tray(on_show=lambda: None, on_copy=lambda: None,
                      on_settings=lambda: None, on_quit=lambda: None,
                      on_recent=lambda: 1 / 0)
        labels = [s[0] if s else None for s in t._menu_spec()]
        self.assertEqual(labels[0], '显示窗口')
        self.assertIn('复制最近文档', labels)

    def test_refresh_is_idempotent(self):
        t, _ = self._tray([('a.docx', '/a.docx')])
        t._build_menu = lambda: None
        t._status_icon = object()          # 走 StatusIcon 分支，不碰真控件
        self.assertTrue(t.refresh_menu())   # 第一次：算变化（还没发过）
        self.assertFalse(t.refresh_menu())  # 内容一样：不该再重建
        self.assertTrue(t.refresh_menu() is False)

    def test_refresh_detects_change(self):
        recent = [('a.docx', '/a.docx')]
        t, _ = self._tray(recent)
        t._build_menu = lambda: None
        t._status_icon = object()
        t.refresh_menu()
        recent.append(('b.docx', '/b.docx'))
        self.assertTrue(t.refresh_menu())


@_need_gi
class SniSetItemsTest(unittest.TestCase):
    """菜单热更新：面板开着的时候「最近文档」也得跟着变。"""

    def _item(self, on_refresh=None):
        return sni.SniItem(
            [('显示窗口', lambda: None), None, ('退出', lambda: None)],
            on_activate=lambda: None, on_refresh=on_refresh)

    def test_set_items_rebuilds_nodes(self):
        item = self._item()
        with mock.patch.object(item, '_emit_layout') as emit:
            self.assertTrue(item.set_items(
                [('显示窗口', lambda: None), None,
                 ('a.docx', lambda: None), ('b.docx', lambda: None)]))
            emit.assert_called_once()
        self.assertEqual(len(item._root_children), 4)
        self.assertEqual(item._props_of(3)['label'], 'a.docx')
        self.assertEqual(item._revision, 2)

    def test_same_labels_no_signal(self):
        item = self._item()
        with mock.patch.object(item, '_emit_layout') as emit:
            self.assertFalse(item.set_items(
                [('显示窗口', lambda: None), None, ('退出', lambda: None)]))
            emit.assert_not_called()

    def test_about_to_show_triggers_refresh(self):
        seen = []
        item = self._item(on_refresh=lambda: seen.append(1) or True)
        inv = _FakeInvocation()
        # AboutToShow 的返回值必须是 (b)，面板据此决定要不要重取布局
        item._on_menu_call(None, None, None, None, 'AboutToShow',
                           mock.Mock(unpack=lambda: (0, True)), inv)
        self.assertEqual(seen, [1])
        self.assertIsNotNone(inv.value)

    def test_emit_layout_survives_no_connection(self):
        item = self._item()
        item._conn = None
        item._emit_layout()          # 没连上总线时不能抛


@_need_gi
class SniClickTest(unittest.TestCase):
    """面板点菜单项 → ``Event('clicked')`` → 对应回调。

    id 错一位就会复制错文件，而这种 bug 在界面上完全看不出来（点了有反应，
    只是复制到了别的文档）。这里把「第 N 项对应哪个回调」钉死。
    """

    def _item(self):
        calls = []
        items = [('显示窗口', lambda: calls.append('show')),
                 None,
                 ('a.docx', lambda: calls.append('a')),
                 ('b.docx', lambda: calls.append('b'))]
        return sni.SniItem(items, on_activate=lambda: None), calls

    @staticmethod
    def _drain():
        # 回调是 GLib.idle_add 排的，没有主循环就手动排空
        ctx = sni.GLib.main_context_default()
        while ctx.pending():
            ctx.iteration(False)

    def test_click_runs_matching_callback(self):
        # id 从 1 开始依次递增：1=显示窗口, 2=分隔线, 3=a.docx, 4=b.docx
        item, calls = self._item()
        item._click(3)
        self._drain()
        self.assertEqual(calls, ['a'])

    def test_click_on_separator_does_nothing(self):
        item, calls = self._item()
        item._click(2)
        self._drain()
        self.assertEqual(calls, [])

    def test_click_unknown_id_does_not_raise(self):
        item, calls = self._item()
        item._click(99)
        self._drain()
        self.assertEqual(calls, [])

    def test_event_method_dispatches(self):
        item, calls = self._item()
        inv = _FakeInvocation()
        params = mock.Mock(unpack=lambda: (4, 'clicked', mock.Mock(), 0))
        item._on_menu_call(None, None, None, None, 'Event', params, inv)
        self._drain()
        self.assertEqual(calls, ['b'])
        # 必须应答（应答值就是 None），否则面板那边的右键菜单会一直卡着
        self.assertIsNone(inv.value)
        self.assertIsNone(inv.error)

    def test_items_in_menu_match_callbacks(self):
        # 托盘菜单里「最近文档」那几行在分隔线之后，点第三项要拿到第二份文档
        item, calls = self._item()
        self.assertEqual(item._props_of(3)['label'], 'a.docx')
        self.assertEqual(item._props_of(4)['label'], 'b.docx')


@_need_gi
class TrayCopyPathTest(unittest.TestCase):
    """托盘点击把路径原样交给上层；上层负责「单路径 / 列表」归一。"""

    def _tray(self, **kw):
        base = dict(on_show=lambda: None, on_copy=lambda: None,
                    on_settings=lambda: None, on_quit=lambda: None)
        base.update(kw)
        return tray.Tray(**base)

    def test_path_passed_through(self):
        seen = []
        self._tray(on_copy_path=seen.append)._copy_path('/x/a.docx')
        self.assertEqual(seen, ['/x/a.docx'])

    def test_fallback_to_copy_latest(self):
        # 没配 on_copy_path 时退回「复制最近一个」，不能静默什么都不做
        hits = []
        self._tray(on_copy=lambda: hits.append(1))._copy_path('/x/a.docx')
        self.assertEqual(hits, [1])

    def test_callback_exception_is_swallowed(self):
        def boom(_p):
            raise RuntimeError('x')
        self._tray(on_copy_path=boom)._copy_path('/x/a.docx')   # 不能抛出去


@_need_gi
class TrayBackendTest(unittest.TestCase):
    def _tray(self):
        return tray.Tray(on_show=lambda: None, on_copy=lambda: None,
                         on_settings=lambda: None, on_quit=lambda: None)

    def test_menu_spec_order(self):
        spec = self._tray()._menu_spec()
        labels = [s[0] if s else None for s in spec]
        self.assertEqual(labels, ['显示窗口', '复制最近文档', None,
                                  '设置…', None, '退出'])

    def test_auto_prefers_sni(self):
        t = self._tray()
        order = []
        t._start_sni = lambda: order.append('sni') or True
        t._start_indicator = lambda: order.append('indicator') or True
        t._start_status_icon = lambda: order.append('statusicon') or True
        with mock.patch.object(tray.core, 'conf', lambda *a, **k: 'auto'):
            self.assertTrue(t.start())
        self.assertEqual(order, ['sni'])

    def test_falls_back_in_order(self):
        t = self._tray()
        order = []

        def fail(name):
            def f():
                order.append(name)
                return False
            return f

        t._start_sni = fail('sni')
        t._start_indicator = fail('indicator')
        t._start_status_icon = lambda: order.append('statusicon') or True
        with mock.patch.object(tray.core, 'conf', lambda *a, **k: 'auto'):
            self.assertTrue(t.start())
        self.assertEqual(order, ['sni', 'indicator', 'statusicon'])

    def test_explicit_backend_goes_first(self):
        t = self._tray()
        order = []
        t._start_sni = lambda: order.append('sni') or True
        t._start_status_icon = lambda: order.append('statusicon') or True
        t._start_indicator = lambda: order.append('indicator') or False
        with mock.patch.object(tray.core, 'conf', lambda *a, **k: 'statusicon'):
            self.assertTrue(t.start())
        self.assertEqual(order, ['statusicon'])

    def test_all_fail(self):
        t = self._tray()
        for name in ('sni', 'indicator', 'statusicon'):
            setattr(t, tray._STARTERS[name], lambda: False)
        with mock.patch.object(tray.core, 'conf', lambda *a, **k: 'auto'):
            self.assertFalse(t.start())
        self.assertFalse(t.available)

    def test_exception_in_backend_does_not_abort(self):
        t = self._tray()

        def boom():
            raise RuntimeError('x')

        t._start_sni = boom
        t._start_indicator = lambda: True
        t.backend = 'indicator'
        with mock.patch.object(tray.core, 'conf', lambda *a, **k: 'auto'):
            self.assertTrue(t.start())


if __name__ == '__main__':
    unittest.main()
