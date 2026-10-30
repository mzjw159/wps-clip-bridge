#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""原生 StatusNotifierItem：自己把托盘图标挂到面板上。

为什么不用 libappindicator（1.5.0 之前的做法）：

1. 它把 ``ItemIsMenu`` 固定成 true —— 只要设了菜单，面板收到左键就只会
   弹菜单，「点一下图标显示窗口」这一层根本没有对应语义；
   ``set_secondary_activate_target`` 也只有中键在部分面板上生效，于是
   用户「双击/单击图标都没反应」。
2. 它只把 ``IconName`` 传给面板，值还是绝对路径
   （``/usr/share/icons/hicolor/512x512/apps/wps-file.png``）。面板若按
   图标主题去查（麒麟面板就是这么查的），查不到就退回系统默认的齿轮图标；
   本机因为图标主题缓存里恰好有记录才正常，装到别人机器上就露馅。
   规范里还有 ``IconPixmap``：直接把像素塞给面板，完全绕开图标主题。

所以这里按规范自己导出两个接口：

* ``org.kde.StatusNotifierItem`` —— ``Activate`` 直接指向「显示窗口」，
  ``ItemIsMenu=false`` 让面板左键走 Activate 而不是弹菜单；
* ``com.canonical.dbusmenu`` —— 右键菜单交给面板渲染（面板自己画，样式
  跟系统其它托盘菜单一致）。
"""
import os

import gi

gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from . import core  # noqa: E402

SNI_PATH = '/StatusNotifierItem'
MENU_PATH = '/MenuBar'
WATCHER_NAME = 'org.kde.StatusNotifierWatcher'
WATCHER_PATH = '/StatusNotifierWatcher'
ITEM_TITLE = 'WPS 最近文档'

# 面板图标一般是 22~24px（2 倍屏再乘 2），多给几个尺寸让面板挑，避免缩放糊
ICON_SIZES = (22, 24, 32, 48)

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


# ------------------------------------------------------------------ 图标
def _argb32(pb):
    """GdkPixbuf(RGBA) -> SNI 要的 ARGB32（网络字节序、非预乘）。"""
    w, h = pb.get_width(), pb.get_height()
    stride = pb.get_rowstride()
    data = pb.get_pixels()
    buf = bytearray(w * h * 4)
    for y in range(h):
        row = y * stride
        dst = y * w * 4
        for x in range(w):
            s = row + x * 4
            d = dst + x * 4
            buf[d] = data[s + 3]      # A
            buf[d + 1] = data[s]      # R
            buf[d + 2] = data[s + 1]  # G
            buf[d + 3] = data[s + 2]  # B
    return bytes(buf)


def _fallback_pixbuf(size):
    """图标文件一个都找不到时的兜底：画一个紫底白字的方块。

    宁可是个能认出来的紫块，也不要面板退回系统默认的齿轮图标 ——
    那会让人以为托盘上挂的是别的程序。
    """
    try:
        import cairo
        from gi.repository import GdkPixbuf
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surf)
        r = size * 0.22
        ctx.set_source_rgb(0.61, 0.26, 0.97)
        ctx.arc(r, r, r, 3.14159, 3.14159 * 1.5)
        ctx.arc(size - r, r, r, 3.14159 * 1.5, 3.14159 * 2)
        ctx.arc(size - r, size - r, r, 0, 3.14159 * 0.5)
        ctx.arc(r, size - r, r, 3.14159 * 0.5, 3.14159)
        ctx.close_path()
        ctx.fill()
        # 中间两条白线，和 wps-file 图标的「文档」意思对上
        ctx.set_source_rgb(1, 1, 1)
        w = size * 0.42
        h = size * 0.09
        x = (size - w) / 2
        for i in (0.36, 0.56):
            ctx.rectangle(x, size * i, w, h)
        ctx.fill()
        surf.flush()
        # 交给 Gdk 转换：cairo 的 ARGB32 是小端 BGRA，直接塞给 Pixbuf 会红蓝互换
        try:
            gi.require_version('Gdk', '3.0')
        except Exception:
            pass
        from gi.repository import Gdk
        return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)
    except Exception as ex:
        core.log('SNI 兜底图标也画不出来: %r' % (ex,))
        return None


def icon_pixmaps(sizes=ICON_SIZES):
    """生成 [(宽, 高, 像素)]，供 IconPixmap 属性使用。"""
    from . import gui
    out = []
    for sz in sizes:
        try:
            pbs = gui._icon_pixbufs((sz,))
        except Exception as ex:
            core.log('SNI 取图标失败(%d): %r' % (sz, ex))
            pbs = []
        if not pbs:
            pb = _fallback_pixbuf(sz)
            if pb is None:
                continue
        else:
            pb = pbs[0]
        try:
            if not pb.get_has_alpha():
                pb = pb.add_alpha(False, 0xFF, 0xFF, 0xFF)
            out.append((pb.get_width(), pb.get_height(), _argb32(pb)))
        except Exception as ex:
            core.log('SNI 转换图标失败(%d): %r' % (sz, ex))
    return out


# ------------------------------------------------------------------ 菜单节点
def _prop_variant(value):
    if isinstance(value, bool):
        return GLib.Variant('b', value)
    if isinstance(value, int):
        return GLib.Variant('i', value)
    return GLib.Variant('s', str(value))


def _node_variant(iid, props, children=()):
    """一帧菜单项。children 里放已构造好的 (ia{sv}av) 变体。"""
    return GLib.Variant(
        '(ia{sv}av)',
        (int(iid), {k: _prop_variant(v) for k, v in props.items()}, list(children)))


class SniItem(object):
    """一个托盘图标。

    ``items`` 形如 ``[('显示窗口', cb), ('复制最近文档', cb), None, ...]``，
    None 表示分隔线；菜单由面板按 dbusmenu 自己渲染。
    """

    def __init__(self, items, on_activate, on_secondary=None,
                 title=ITEM_TITLE, icon_name='wps-file'):
        self._items = list(items)
        self.on_activate = on_activate
        self.on_secondary = on_secondary or on_activate
        self.title = title
        self.icon_name = icon_name
        self.available = False
        self._conn = None
        self._regs = []
        self._sub = None
        self._pixmaps = None
        self._revision = 1
        self._name = 'org.kde.StatusNotifierItem-%d-1' % os.getpid()
        # id -> (props, callback)
        self._nodes = {}
        self._root_children = []
        self._build()

    # ------------------------------------------------------------ 菜单
    def _build(self):
        iid = 1
        for item in self._items:
            if item is None:
                self._nodes[iid] = ({'type': 'separator'}, None)
                self._root_children.append(
                    _node_variant(iid, {'type': 'separator'}))
                iid += 1
                continue
            label, cb = item
            props = {'label': label, 'enabled': True, 'visible': True}
            self._nodes[iid] = (props, cb)
            self._root_children.append(_node_variant(iid, props))
            iid += 1

    def _root(self):
        return (0, {'children-display': GLib.Variant('s', 'submenu')},
                self._root_children)

    # ------------------------------------------------------------ 生命周期
    def start(self):
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as ex:
            core.log('SNI：没有可用的会话总线: %r' % (ex,))
            return False
        if conn is None:
            core.log('SNI：会话总线为空')
            return False
        try:
            sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
            menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
            self._regs.append(conn.register_object(
                SNI_PATH, sni_info, self._on_item_call, self._on_item_prop, None))
            self._regs.append(conn.register_object(
                MENU_PATH, menu_info, self._on_menu_call, None, None))
        except Exception as ex:
            core.log('SNI：导出 D-Bus 接口失败: %r' % (ex,))
            self._teardown(conn)
            return False

        if not self._has_owner(conn, WATCHER_NAME):
            core.log('SNI：面板没有提供 %s，改用其它后端' % WATCHER_NAME)
            self._teardown(conn)
            return False

        try:
            res = conn.call_sync(
                'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'RequestName',
                GLib.Variant('(su)', (self._name, 0)), None,
                Gio.DBusCallFlags.NONE, 3000, None)
            state = res.unpack()[0]
            if state not in (1, 4):          # 1=成为主拥有者 4=本来就是自己
                core.log('SNI：占用总线名 %s 失败（%s）' % (self._name, state))
                self._teardown(conn)
                return False
        except Exception as ex:
            core.log('SNI：占用总线名失败: %r' % (ex,))
            self._teardown(conn)
            return False

        if not self._register_with_watcher(conn):
            self._teardown(conn)
            return False

        # 面板重启（登录后重新拉起、崩溃重启）会重新成为 watcher 的拥有者，
        # 这时得再报到一次，否则图标就这么永远消失了。
        try:
            self._sub = conn.signal_subscribe(
                'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                '/org/freedesktop/DBus', WATCHER_NAME, Gio.DBusSignalFlags.NONE,
                self._on_watcher_changed)
        except Exception:
            self._sub = None

        self._conn = conn
        self.available = True
        core.log('SNI：已注册为 %s' % self._name)
        return True

    def _register_with_watcher(self, conn):
        try:
            conn.call_sync(
                WATCHER_NAME, WATCHER_PATH, WATCHER_NAME,
                'RegisterStatusNotifierItem', GLib.Variant('(s)', (self._name,)),
                None, Gio.DBusCallFlags.NONE, 3000, None)
            return True
        except Exception as ex:
            core.log('SNI：向 %s 报到失败: %r' % (WATCHER_NAME, ex))
            return False

    @staticmethod
    def _has_owner(conn, name):
        try:
            res = conn.call_sync(
                'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner',
                GLib.Variant('(s)', (name,)), None,
                Gio.DBusCallFlags.NONE, 2000, None)
            return bool(res.unpack()[0])
        except Exception:
            return False

    def _teardown(self, conn):
        for rid in self._regs:
            try:
                conn.unregister_object(rid)
            except Exception:
                pass
        self._regs = []
        try:
            conn.call_sync(
                'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'ReleaseName',
                GLib.Variant('(s)', (self._name,)), None,
                Gio.DBusCallFlags.NONE, 1000, None)
        except Exception:
            pass
        self.available = False

    def stop(self):
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        if self._sub is not None:
            try:
                conn.signal_unsubscribe(self._sub)
            except Exception:
                pass
            self._sub = None
        self._teardown(conn)

    # ------------------------------------------------------------ 回调
    def _on_watcher_changed(self, conn, _sender, _path, _iface, _signal, params):
        try:
            _name, old, new = params.unpack()
        except Exception:
            return
        if new:
            core.log('SNI：面板（watcher）重新出现，重新报到一次')
            try:
                self._register_with_watcher(conn)
            except Exception as ex:
                core.log('SNI：重新报到失败: %r' % (ex,))
        return

    @staticmethod
    def _fire(cb):
        try:
            cb()
        except Exception as ex:
            core.log('托盘回调失败: %r' % (ex,))
        return False

    # ------------------------------------------------------------ SNI 方法
    def _on_item_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        # 面板上的左键 / 中键：统一「显示窗口」。这是原生实现最要紧的一条，
        # libappindicator 派下来的左键只会弹菜单。
        if method == 'Activate':
            GLib.idle_add(self._fire, self.on_activate)
        elif method == 'SecondaryActivate':
            GLib.idle_add(self._fire, self.on_secondary)
        # ContextMenu：菜单由面板自己渲染，这里只需应答
        invocation.return_value(None)

    def _on_item_prop(self, _conn, _sender, _path, _iface, prop):
        if self._pixmaps is None:
            self._pixmaps = icon_pixmaps()
        props = {
            'Category': GLib.Variant('s', 'ApplicationStatus'),
            'Id': GLib.Variant('s', 'wps-clip-bridge'),
            'Title': GLib.Variant('s', self.title),
            'Status': GLib.Variant('s', 'Active'),
            'WindowId': GLib.Variant('i', 0),
            'IconThemePath': GLib.Variant('s', ''),
            # 主题名留着：认图标主题的面板能用矢量图，更清晰
            'IconName': GLib.Variant('s', self.icon_name or ''),
            # 同时给像素：不看图标主题的面板（麒麟面板）也不会退回齿轮图标
            'IconPixmap': GLib.Variant('a(iiay)', self._pixmaps),
            'OverlayIconName': GLib.Variant('s', ''),
            'OverlayIconPixmap': GLib.Variant('a(iiay)', []),
            'AttentionIconName': GLib.Variant('s', ''),
            'AttentionIconPixmap': GLib.Variant('a(iiay)', self._pixmaps),
            'AttentionMovieName': GLib.Variant('s', ''),
            'ToolTip': GLib.Variant('(sa(iiay)ss)',
                                    ('', self._pixmaps, self.title, '单击显示窗口')),
            # False = 左键激活而不是弹菜单
            'ItemIsMenu': GLib.Variant('b', False),
            'Menu': GLib.Variant('o', MENU_PATH),
        }
        return props.get(prop)

    # ------------------------------------------------------------ 菜单方法
    def _on_menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            if method == 'GetLayout':
                invocation.return_value(
                    GLib.Variant('(u(ia{sv}av))', (self._revision, self._root())))
            elif method == 'GetGroupProperties':
                ids = params.unpack()[0]
                out = []
                for iid in ids:
                    props = self._props_of(int(iid))
                    if props is None:
                        continue
                    out.append((int(iid),
                                {k: _prop_variant(v) for k, v in props.items()}))
                invocation.return_value(GLib.Variant('a(ia{sv})', out))
            elif method == 'GetProperty':
                iid, name = params.unpack()
                props = self._props_of(int(iid)) or {}
                value = props.get(name)
                if value is None:
                    invocation.return_dbus_error(
                        'org.freedesktop.DBus.Error.InvalidArgs',
                        'no such property: %s' % name)
                    return
                invocation.return_value(GLib.Variant('(v)', (_prop_variant(value),)))
            elif method == 'Event':
                iid, event, _data, _ts = params.unpack()
                if str(event) == 'clicked':
                    self._click(int(iid))
                invocation.return_value(None)
            elif method == 'EventGroup':
                for (iid, event, _data, _ts) in params.unpack()[0]:
                    if str(event) == 'clicked':
                        self._click(int(iid))
                invocation.return_value(GLib.Variant('(ai)', ([],)))
            elif method == 'AboutToShow':
                invocation.return_value(GLib.Variant('(b)', (False,)))
            elif method == 'AboutToShowGroup':
                invocation.return_value(GLib.Variant('(aiai)', ([], [])))
            else:
                invocation.return_dbus_error(
                    'org.freedesktop.DBus.Error.UnknownMethod', method)
        except Exception as ex:
            core.log('SNI 菜单调用 %s 失败: %r' % (method, ex))
            try:
                invocation.return_dbus_error(
                    'org.freedesktop.DBus.Error.Failed', str(ex))
            except Exception:
                pass

    def _props_of(self, iid):
        if iid == 0:
            return {'children-display': 'submenu'}
        node = self._nodes.get(iid)
        return node[0] if node else None

    def _click(self, iid):
        node = self._nodes.get(iid)
        if not node:
            return
        props, cb = node
        if props.get('type') == 'separator' or cb is None:
            return
        GLib.idle_add(self._fire, cb)
