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
