#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""系统托盘图标。

三条后端，按可靠性排序：

1. ``sni`` —— 自己实现的 StatusNotifierItem（见 sni.py）。单击/双击/中键
   都直接「显示窗口」，图标用 IconPixmap 直接给像素，不依赖图标主题缓存。
2. ``indicator`` —— libappindicator。以前的主力，现在只当兜底：它不允许
   左键激活窗口（只会弹菜单），也只把绝对路径当图标名传给面板，面板按
   图标主题一查就落空、显示成系统齿轮图标。
3. ``statusicon`` —— GTK 老式 XEmbed 图标，只有不支持 SNI 的老面板才用得上。

都失败也不致命——上层据此避免「关窗后无处可点」。
"""
import os

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from . import core  # noqa: E402

_ID = 'wps-clip-bridge'

# 顺序即优先级；'auto' 与其写错时的兜底都走这一串
_BACKENDS = ('sni', 'indicator', 'statusicon')
_STARTERS = {'sni': '_start_sni',
             'indicator': '_start_indicator',
             'statusicon': '_start_status_icon'}


def _icon_pixbuf(size=48):
    from . import gui
    pbs = gui._icon_pixbufs((size,))
    return pbs[0] if pbs else None


def _icon_arg():
    """AppIndicator 吃绝对路径，源码树里也能显示（它没有 IconPixmap 可用）。"""
    from . import gui
    for p in gui._icon_files():
        if p.endswith('.png') and os.path.exists(p):
            return p
    return gui.APP_ICON


class Tray(object):
    def __init__(self, on_show, on_copy, on_settings, on_quit):
        self.on_show = on_show
        self.on_copy = on_copy
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.backend = None
        self._sni = None
        self._indicator = None
        self._ai = None
        self._status_icon = None
        self._menu = None
        self._show_item = None

    # ------------------------------------------------------------ 构建
    def _menu_spec(self):
        """菜单内容：Gtk 菜单和 dbusmenu 共用一份定义，改文案只改这里。"""
        return [
            ('显示窗口', self.on_show),
            ('复制最近文档', self.on_copy),
            None,
            ('设置…', self.on_settings),
            None,
            ('退出', self.on_quit),
        ]

    def _build_menu(self):
        menu = Gtk.Menu()
        for entry in self._menu_spec():
            if entry is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            label, cb = entry
            mi = Gtk.MenuItem(label=label)
            mi.connect('activate', lambda _w, cb=cb: cb())
            if label == '显示窗口':
                # 给 AppIndicator 当「次级激活」目标：中键点图标会走到这里
                self._show_item = mi
            menu.append(mi)
        menu.show_all()
        self._menu = menu
        return menu

    def start(self):
        want = core.conf('ui', 'tray_backend', 'auto').strip().lower()
        if want in ('sni', 'indicator', 'statusicon'):
            order = (want,) + tuple(b for b in _BACKENDS if b != want)
        else:
            order = _BACKENDS
        for name in order:
            starter = getattr(self, _STARTERS.get(name, '_start_' + name), None)
            if starter is None:
                continue
            try:
                if starter():
                    return True
            except Exception as ex:
                core.log('托盘后端 %s 启动异常: %r' % (name, ex))
        core.log('托盘初始化失败：面板可能不支持 StatusNotifier / XEmbed')
        return False

    # ------------------------------------------------------------ 后端 1
    def _start_sni(self):
        try:
            from .sni import SniItem
        except Exception as ex:
            core.log('SNI 模块不可用: %r' % (ex,))
            return False
        item = SniItem(self._menu_spec(), on_activate=self.on_show,
                       on_secondary=self.on_show)
        if not item.start():
            return False
        self._sni = item
        self.backend = 'sni'
        core.log('托盘使用原生 StatusNotifierItem（单击图标即显示窗口）')
        return True

    # ------------------------------------------------------------ 后端 2
    def _start_indicator(self):
        # 注意别用 __import__('gi.repository', fromlist=[...])：它在某些
        # PyGObject 版本上拿不到子模块，抛 ImportError 把回退链打断。
        import gi.repository as repo
        for name in ('AyatanaAppIndicator3', 'AppIndicator3'):
            try:
                gi.require_version(name, '0.1')
            except Exception:
                continue
            AppIndicator = getattr(repo, name, None)
            if AppIndicator is None:
                try:
                    AppIndicator = __import__(
                        'gi.repository.' + name, fromlist=[name]).__dict__[name]
                except Exception:
                    AppIndicator = None
            if AppIndicator is None:
                continue
            if self._new_indicator(AppIndicator, name):
                return True
        return False

    def _new_indicator(self, AppIndicator, name):
        try:
            ind = AppIndicator.Indicator.new(
                _ID, _icon_arg(), AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            ind.set_title('WPS 最近文档')
            menu = self._build_menu()
            ind.set_menu(menu)

            # StatusNotifier 协议里没有「双击」这个语义：面板对左键的处理就是
            # 弹菜单（这是面板决定的，程序改不了）。协议里唯一能表达「点一下
            # 图标但不要菜单」的是 secondary activate —— 中键一定生效，部分
            # 面板也把双击转发给它。
            item = getattr(self, '_show_item', None)
            if item is not None and hasattr(ind, 'set_secondary_activate_target'):
                try:
                    ind.set_secondary_activate_target(item)
                except Exception as ex:
                    core.log('设置次级激活目标失败: %r' % (ex,))

            self._indicator = ind
            self._ai = AppIndicator
            self.backend = 'indicator'
            core.log('托盘使用 %s' % name)
            return True
        except Exception as ex:
            core.log('AppIndicator 初始化失败: %r' % (ex,))
            return False

    # ------------------------------------------------------------ 后端 3
    def _start_status_icon(self):
        if not hasattr(Gtk, 'StatusIcon'):
            return False
        try:
            pb = _icon_pixbuf(48)
            icon = Gtk.StatusIcon()
            if pb is not None:
                icon.set_from_pixbuf(pb)
            else:
                icon.set_from_file(_icon_arg())
            icon.set_tooltip_text('WPS 最近文档（单击显示窗口）')
            icon.connect('activate', lambda *a: self.on_show())
            icon.connect('popup-menu', self._on_popup)
            icon.set_visible(True)
            self._status_icon = icon
            self.backend = 'statusicon'
            core.log('托盘使用 Gtk.StatusIcon')
            return True
        except Exception as ex:
            core.log('StatusIcon 初始化失败: %r' % (ex,))
            return False

    def _on_popup(self, icon, button, activate_time):
        menu = self._menu or self._build_menu()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon,
                   button, activate_time)

    # ------------------------------------------------------------ 控制
    @property
    def available(self):
        return self.backend is not None

    def stop(self):
        try:
            if self._sni is not None:
                self._sni.stop()
        except Exception:
            pass
        self._sni = None
        try:
            if self._indicator is not None and self._ai is not None:
                self._indicator.set_status(self._ai.IndicatorStatus.PASSIVE)
        except Exception:
            pass
        self._indicator = None
        self._ai = None
        try:
            if self._status_icon is not None:
                self._status_icon.set_visible(False)
        except Exception:
            pass
        self._status_icon = None
        self.backend = None
