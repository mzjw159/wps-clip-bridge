#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""系统托盘图标。

两条后端，优先用 StatusNotifier（AppIndicator），因为它在新面板上更稳；
面板不支持 SNI 时 libappindicator 自己会退回 XEmbed，还不行就用 GTK 的
`Gtk.StatusIcon`。都失败也不致命——上层据此避免「关窗后无处可点」。
"""
import os

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from . import core  # noqa: E402

_ID = 'wps-clip-bridge'


def _icon_pixbuf(size=48):
    from . import gui
    pbs = gui._icon_pixbufs((size,))
    return pbs[0] if pbs else None


def _icon_arg():
    """AppIndicator 优先吃绝对路径，源码树里也能显示。"""
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
        self._indicator = None
        self._ai = None
        self._status_icon = None
        self._menu = None

    # ------------------------------------------------------------ 构建
    def _build_menu(self):
        menu = Gtk.Menu()
        for label, cb in (
            ('显示窗口', self.on_show),
            ('复制最近文档', self.on_copy),
            (None, None),
            ('设置…', self.on_settings),
            (None, None),
            ('退出', self.on_quit),
        ):
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.connect('activate', lambda _w, cb=cb: cb())
            menu.append(mi)
        menu.show_all()
        self._menu = menu
        return menu

    def start(self):
        want = core.conf('ui', 'tray_backend', 'auto').strip().lower()
        if want in ('auto', 'indicator'):
            if self._start_indicator():
                return True
        if want in ('auto', 'statusicon'):
            if self._start_status_icon():
                return True
        core.log('托盘初始化失败：面板可能不支持 StatusNotifier / XEmbed')
        return False

    def _start_indicator(self):
        try:
            gi.require_version('AppIndicator3', '0.1')
            from gi.repository import AppIndicator3 as AppIndicator
        except Exception:
            return False
        try:
            ind = AppIndicator.Indicator.new(
                _ID, _icon_arg(), AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            ind.set_title('WPS 最近文档复制')
            ind.set_menu(self._build_menu())
            self._indicator = ind
            self._ai = AppIndicator
            self.backend = 'indicator'
            core.log('托盘使用 AppIndicator3')
            return True
        except Exception as ex:
            core.log('AppIndicator 初始化失败: %r' % (ex,))
            return False

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
            icon.set_tooltip_text('WPS 最近文档复制')
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
