#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""「WPS 最近文档」选择窗口。

为什么要单独做这个窗口：WPS for Linux 在文档列表里右键「复制」时只拿下
剪贴板所有权、不写任何数据（实测 TARGETS 只有 4 个管理性 target，12 种格式
盲请求全为空）。所以那个按钮在外部无法补救，只能绕开它：
直接读 WPS 自己的最近文档记录，把文件放进剪贴板。
"""
import os
import sys

os.environ.setdefault('GDK_BACKEND', 'x11')

from . import core  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from . import recent, service  # noqa: E402

WAKE_POLL_MS = 400


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return str(n)


def _load_app_icon():
    """加载应用图标：优先主题名，其次按安装路径/源码路径直接读文件。

    历史 bug：图标名曾为 wps-clip-bridge，换成 wps-file 后这里没同步，
    导致窗口/任务栏/托盘显示默认图标。多级回退保证装到哪儿都正确。
    """
    try:
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon('wps-file'):
            return theme.load_icon('wps-file', 128, 0)
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (
        '/usr/share/icons/hicolor/scalable/apps/wps-file.svg',
        '/usr/share/icons/hicolor/256x256/apps/wps-file.png',
        '/usr/share/icons/hicolor/128x128/apps/wps-file.png',
        '/usr/share/icons/hicolor/64x64/apps/wps-file.png',
        '/usr/share/icons/hicolor/48x48/apps/wps-file.png',
        os.path.join(here, '..', '..', 'data', 'wps-file.svg'),
        os.path.join(here, '..', '..', 'data', 'wps-file.png'),
    ):
        try:
            if os.path.exists(p):
                return GdkPixbuf.Pixbuf.new_from_file(p)
        except Exception:
            continue
    return None


class App(object):
    def __init__(self):
        self.rows = []
        self.wake_mtime = core.mtime(core.gui_wake_path())

        self.win = Gtk.Window(title='WPS 最近文档 · 一键复制')
        self.win.set_default_size(780, 540)
        self.win.set_border_width(12)
        _icon = _load_app_icon()
        if _icon is not None:
            self.win.set_icon(_icon)
        else:
            self.win.set_icon_name('wps-file')
        self.win.connect('destroy', Gtk.main_quit)
        self.win.connect('key-press-event', self.on_key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.win.add(box)

        # 顶部：搜索 + 刷新
        top = Gtk.Box(spacing=6)
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text('搜索文件名或文件夹…')
        self.entry.connect('search-changed', self.on_search)
        top.pack_start(self.entry, True, True, 0)
        refresh = Gtk.Button(label='刷新')
        refresh.connect('clicked', lambda *a: self.reload())
        top.pack_start(refresh, False, False, 0)
        box.pack_start(top, False, False, 0)

        # 列表
        self.store = Gtk.ListStore(str, str, str, str, str)  # 类型 文件名 目录 大小 路径
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self.filter_func)

        view = Gtk.TreeView(model=self.filter)
        view.set_headers_visible(True)
        for title, idx, width in (('类型', 0, 56), ('文件名', 1, 330),
                                  ('大小', 3, 84), ('所在文件夹', 2, 320)):
            r = Gtk.CellRendererText()
            r.set_property('ellipsize', 3)
            col = Gtk.TreeViewColumn(title, r, text=idx)
            col.set_fixed_width(width)
            col.set_resizable(True)
            col.set_sort_column_id(idx)
            view.append_column(col)
        view.connect('row-activated', self.on_activate)
        view.get_selection().connect('changed', self.on_select)
        self.view = view

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        # 底部
        bottom = Gtk.Box(spacing=8)
        self.copy_btn = Gtk.Button(label='复制选中文件')
        self.copy_btn.get_style_context().add_class('suggested-action')
        self.copy_btn.set_sensitive(False)
        self.copy_btn.connect('clicked', self.on_copy_clicked)
        bottom.pack_start(self.copy_btn, False, False, 0)

        open_btn = Gtk.Button(label='打开所在文件夹')
        open_btn.connect('clicked', self.on_open_dir)
        bottom.pack_start(open_btn, False, False, 0)

        browse_btn = Gtk.Button(label='浏览本地文件…')
        browse_btn.set_tooltip_text('列表里没有的文件，可以直接选（支持多选）')
        browse_btn.connect('clicked', self.on_browse)
        bottom.pack_start(browse_btn, False, False, 0)
        box.pack_start(bottom, False, False, 0)

        self.status = Gtk.Label(label='选一个文档，双击即可复制到剪贴板')
        self.status.set_xalign(0)
        self.status.set_ellipsize(3)
        box.pack_start(self.status, False, False, 0)

        self.reload()
        self.win.show_all()
        GLib.timeout_add(WAKE_POLL_MS, self.poll_wake)

    # ------------------------------------------------ 数据
    def reload(self):
        self.store.clear()
        self.rows = recent.parse_recent(limit=core.conf_int('ui', 'limit', 80))
        for p in self.rows:
            try:
                size = human_size(os.stat(p).st_size)
            except OSError:
                size = '?'
            self.store.append([recent.kind_of(p), os.path.basename(p),
                               os.path.dirname(p), size, p])
        if self.rows:
            self.set_status('共 %d 个最近文档（含云文档本地副本），双击即可复制'
                            % len(self.rows))
        else:
            self.set_status('没读到最近文档。请先用 WPS 打开几个文件，再点「刷新」。')

    def filter_func(self, model, it, _data):
        kw = self.entry.get_text().strip().lower()
        if not kw:
            return True
        return kw in (model[it][1] or '').lower() or kw in (model[it][2] or '').lower()

    def on_search(self, *_):
        self.filter.refilter()

    # ------------------------------------------------ 交互
    def on_select(self, sel):
        self.copy_btn.set_sensitive(sel.count_selected_rows() > 0)

    def current_path(self):
        model, it = self.view.get_selection().get_selected()
        if it is None:
            return None
        return model[it][4]

    def on_activate(self, view, path, column):
        model = view.get_model()
        it = model.get_iter(path)
        self.copy_paths([model[it][4]])

    def on_copy_clicked(self, *_):
        p = self.current_path()
        if not p:
            self.set_status('请先在列表里选中一个文件')
            return
        self.copy_paths([p])

    def on_open_dir(self, *_):
        p = self.current_path()
        if not p:
            return
        try:
            Gtk.show_uri(None, 'file://' + os.path.dirname(p),
                         Gtk.get_current_event_time())
        except Exception as ex:
            self.set_status('打开文件夹失败：%s' % ex)

    def on_browse(self, *_):
        dlg = Gtk.FileChooserDialog(title='选择要复制的文件', parent=self.win,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons('取消', Gtk.ResponseType.CANCEL, '复制', Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        resp = dlg.run()
        files = dlg.get_filenames() if resp == Gtk.ResponseType.OK else []
        dlg.destroy()
        if files:
            self.copy_paths(files)

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.win.destroy()

    def set_status(self, text):
        self.status.set_text(text)

    # ------------------------------------------------ 窗口唤醒
    def poll_wake(self):
        cur = core.mtime(core.gui_wake_path())
        if cur is not None and cur != self.wake_mtime:
            self.wake_mtime = cur
            self.present()
        return True

    def present(self):
        try:
            self.win.deiconify()
            self.win.present()
        except Exception:
            pass

    # ------------------------------------------------ 复制
    def copy_paths(self, paths):
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            self.set_status('没有可复制的文件')
            return
        self.set_status('正在复制…')
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        ok, msg = service.request_copy(paths)
        if ok:
            self.set_status('✔ 已复制：%s —— 切到桌面或微信按 Ctrl+V'
                            % service.describe(paths))
        else:
            self.set_status('✘ %s' % msg)


def main(argv=None):
    # 让窗口 WM_CLASS 与 .desktop 文件名一致，桌面环境才能把运行中的窗口
    # 关联到应用图标（否则任务栏/托盘里显示的是默认图标）
    GLib.set_prgname('wps-clip-bridge')
    si = core.SingleInstance('gui')
    if not si.acquire():
        # 已有窗口在跑，唤醒它
        core.touch(core.gui_wake_path())
        return 0
    try:
        App()
        Gtk.main()
    except KeyboardInterrupt:
        pass
    finally:
        si.release()
    return 0


if __name__ == '__main__':
    sys.exit(main())
