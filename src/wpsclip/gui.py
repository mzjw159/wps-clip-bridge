#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""「WPS 最近文档」选择窗口。

为什么要单独做这个窗口：WPS for Linux 在文档列表里右键「复制」时只拿下
剪贴板所有权、不写任何数据（实测 TARGETS 只有 4 个管理性 target，12 种格式
盲请求全为空）。所以那个按钮在外部无法补救，只能绕开它：
直接读 WPS 自己的最近文档记录，把文件放进剪贴板。

界面要点：
- 左侧「来源」列用人话标注 云端 / 微信 / 本地（原来只显示 云/W/X 看不懂）；
  「类型」列单独标注 文档 / 表格 / 演示 / PDF。
- 列表按修改时间分组：今天 / 昨天 / 近 3 天 / 近 7 天 / 近 30 天 / 更早。
- 右键菜单：打开、打开所在位置、复制、复制路径、移到回收站。
"""
import datetime
import os
import sys

os.environ.setdefault('GDK_BACKEND', 'x11')

from . import core  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import recent, service  # noqa: E402

WAKE_POLL_MS = 400

# TreeStore 列索引
C_ISGROUP, C_ICON, C_NAME, C_SOURCE, C_TYPE, C_SIZE, C_DIR, C_WHEN, C_PATH, C_MTIME = range(10)

# 分组顺序
_BUCKETS = ('今天', '昨天', '近 3 天', '近 7 天', '近 30 天', '更早')

_SOURCE_COLOR = {'云端': '#2f7bd6', '微信': '#07c160', '本地': '#8a8f98'}

CSS = b"""
.toolbar { padding: 10px 12px 2px 12px; }
.bottom-bar { padding: 8px 2px 0 2px; }
.status-label { opacity: 0.72; font-size: 92%; }
headerbar .title { font-weight: 600; }
"""


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return str(n)


def source_of(path):
    """来源：云端 / 微信 / 本地。"""
    low = path.lower()
    if 'wps cloud files' in low or 'wpscloud' in low or '云文档' in path:
        return '云端'
    if 'wechat' in low or 'xwechat_files' in low or '微信' in path:
        return '微信'
    return '本地'


def type_label(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        '.doc': '文档', '.docx': '文档', '.wps': '文档', '.wpt': '文档', '.rtf': '文档',
        '.xls': '表格', '.xlsx': '表格', '.et': '表格', '.ett': '表格', '.csv': '表格',
        '.ppt': '演示', '.pptx': '演示', '.dps': '演示', '.dpt': '演示',
        '.pdf': 'PDF', '.ofd': 'PDF',
        '.txt': '文本', '.md': '文本',
    }.get(ext, '其他')


def type_icon(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.doc', '.docx', '.wps', '.wpt', '.rtf'):
        return 'x-office-document'
    if ext in ('.xls', '.xlsx', '.et', '.ett', '.csv'):
        return 'x-office-spreadsheet'
    if ext in ('.ppt', '.pptx', '.dps', '.dpt'):
        return 'x-office-presentation'
    if ext in ('.pdf', '.ofd'):
        return 'application-pdf'
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
        return 'image-x-generic'
    return 'text-x-generic'


def bucket_of(mt):
    """返回 (分组序号, 分组名)。按自然日计算。"""
    if not mt:
        return (5, _BUCKETS[5])
    days = (datetime.date.today() - datetime.date.fromtimestamp(mt)).days
    if days <= 0:
        return (0, _BUCKETS[0])
    if days == 1:
        return (1, _BUCKETS[1])
    if days <= 3:
        return (2, _BUCKETS[2])
    if days <= 7:
        return (3, _BUCKETS[3])
    if days <= 30:
        return (4, _BUCKETS[4])
    return (5, _BUCKETS[5])


def when_str(mt):
    if not mt:
        return ''
    dt = datetime.datetime.fromtimestamp(mt)
    days = (datetime.date.today() - dt.date()).days
    if days <= 0:
        return dt.strftime('%H:%M')
    if days == 1:
        return '昨天 ' + dt.strftime('%H:%M')
    if days < 7:
        return '周' + '一二三四五六日'[dt.weekday()]
    return dt.strftime('%Y-%m-%d')


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

        self.win = Gtk.Window(title='WPS 最近文档')
        self.win.set_default_size(1000, 640)
        self.win.get_style_context().add_class('wps-window')
        _icon = _load_app_icon()
        if _icon is not None:
            self.win.set_icon(_icon)
        else:
            self.win.set_icon_name('wps-file')
        self.win.connect('destroy', Gtk.main_quit)
        self.win.connect('key-press-event', self.on_key)
        self._load_css()

        # 标题栏
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title('WPS 最近文档')
        hb.set_subtitle('双击复制 · 右键更多操作')
        self.win.set_titlebar(hb)

        self.refresh_btn = Gtk.Button()
        self.refresh_btn.set_image(Gtk.Image.new_from_icon_name(
            'view-refresh-symbolic', Gtk.IconSize.BUTTON))
        self.refresh_btn.set_tooltip_text('刷新列表')
        self.refresh_btn.connect('clicked', lambda *a: self.reload())
        hb.pack_end(self.refresh_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(12)
        self.win.add(box)

        # 搜索栏
        toolbar = Gtk.Box(spacing=8)
        toolbar.get_style_context().add_class('toolbar')
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text('搜索文件名或文件夹…')
        self.entry.connect('search-changed', self.on_search)
        toolbar.pack_start(self.entry, True, True, 0)
        box.pack_start(toolbar, False, False, 0)

        # 列表（两级：分组 -> 文档）
        self.store = Gtk.TreeStore(bool, str, str, str, str, str, str, str, str, float)
        view = Gtk.TreeView(model=self.store)
        view.set_headers_visible(True)
        view.set_enable_tree_lines(False)
        view.set_show_expanders(True)
        view.set_level_indentation(14)
        view.set_grid_lines(Gtk.TreeViewGridLines.HORIZONTAL)

        # 名称（图标 + 文本）
        self.icon_cell = Gtk.CellRendererPixbuf()
        self.icon_cell.set_property('xpad', 2)
        name_cell = Gtk.CellRendererText()
        name_cell.set_property('ellipsize', 3)
        col = Gtk.TreeViewColumn('名称')
        col.pack_start(self.icon_cell, False)
        col.pack_start(name_cell, True)
        col.set_cell_data_func(self.icon_cell, self._name_icon_func)
        col.set_cell_data_func(name_cell, self._name_text_func)
        col.set_fixed_width(320)
        col.set_resizable(True)
        view.append_column(col)

        # 来源
        src_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('来源')
        col.pack_start(src_cell, True)
        col.set_cell_data_func(src_cell, self._source_func)
        col.set_fixed_width(64)
        view.append_column(col)

        # 类型
        type_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('类型')
        col.pack_start(type_cell, True)
        col.set_cell_data_func(type_cell, self._plain_func(C_TYPE))
        col.set_fixed_width(64)
        view.append_column(col)

        # 大小
        size_cell = Gtk.CellRendererText()
        size_cell.set_property('xalign', 1.0)
        col = Gtk.TreeViewColumn('大小')
        col.pack_start(size_cell, True)
        col.set_cell_data_func(size_cell, self._plain_func(C_SIZE))
        col.set_fixed_width(80)
        view.append_column(col)

        # 修改时间
        when_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('修改时间')
        col.pack_start(when_cell, True)
        col.set_cell_data_func(when_cell, self._plain_func(C_WHEN))
        col.set_fixed_width(105)
        view.append_column(col)

        # 所在文件夹
        dir_cell = Gtk.CellRendererText()
        dir_cell.set_property('ellipsize', 3)
        col = Gtk.TreeViewColumn('所在文件夹')
        col.pack_start(dir_cell, True)
        col.set_cell_data_func(dir_cell, self._plain_func(C_DIR))
        col.set_min_width(160)
        view.append_column(col)

        view.connect('row-activated', self.on_activate)
        view.connect('button-press-event', self.on_button_press)
        view.get_selection().connect('changed', self.on_select)
        self.view = view

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        # 底部操作栏
        bottom = Gtk.Box(spacing=8)
        bottom.get_style_context().add_class('bottom-bar')
        self.copy_btn = Gtk.Button(label='复制选中文件')
        self.copy_btn.get_style_context().add_class('suggested-action')
        self.copy_btn.set_sensitive(False)
        self.copy_btn.connect('clicked', self.on_copy_clicked)
        bottom.pack_start(self.copy_btn, False, False, 0)

        open_btn = Gtk.Button(label='打开')
        open_btn.connect('clicked', self.on_open_file)
        bottom.pack_start(open_btn, False, False, 0)

        dir_btn = Gtk.Button(label='打开所在位置')
        dir_btn.connect('clicked', self.on_open_dir)
        bottom.pack_start(dir_btn, False, False, 0)

        browse_btn = Gtk.Button(label='浏览本地文件…')
        browse_btn.set_tooltip_text('列表里没有的文件，可以直接选（支持多选）')
        browse_btn.connect('clicked', self.on_browse)
        bottom.pack_end(browse_btn, False, False, 0)
        box.pack_start(bottom, False, False, 0)

        self.status = Gtk.Label(label='选一个文档，双击即可复制到剪贴板')
        self.status.set_xalign(0)
        self.status.set_ellipsize(3)
        self.status.get_style_context().add_class('status-label')
        box.pack_start(self.status, False, False, 0)

        self.reload()
        self.win.show_all()
        GLib.timeout_add(WAKE_POLL_MS, self.poll_wake)

    # ------------------------------------------------ 样式
    def _load_css(self):
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    # ------------------------------------------------ 单元格渲染
    def _name_icon_func(self, _col, cell, model, it, _data):
        isg = model[it][C_ISGROUP]
        cell.set_property('visible', not isg)
        if not isg:
            cell.set_property('icon-name', model[it][C_ICON])

    def _name_text_func(self, _col, cell, model, it, _data):
        if model[it][C_ISGROUP]:
            count = model[it][C_SIZE] or ''
            cell.set_property('markup', '<b>%s</b>   <span foreground="#9aa0a6">%s</span>'
                              % (GLib.markup_escape_text(model[it][C_NAME]),
                                 GLib.markup_escape_text(count)))
        else:
            cell.set_property('markup',
                              GLib.markup_escape_text(model[it][C_NAME] or ''))

    def _source_func(self, _col, cell, model, it, _data):
        if model[it][C_ISGROUP]:
            cell.set_property('markup', '')
            return
        src = model[it][C_SOURCE]
        color = _SOURCE_COLOR.get(src, '#8a8f98')
        cell.set_property('markup', '<span foreground="%s">%s</span>'
                          % (color, GLib.markup_escape_text(src)))

    def _plain_func(self, idx):
        def func(_col, cell, model, it, _data):
            if model[it][C_ISGROUP]:
                cell.set_property('markup', '')
            else:
                cell.set_property('markup',
                                  GLib.markup_escape_text(model[it][idx] or ''))
        return func

    # ------------------------------------------------ 数据
    def reload(self):
        self.store.clear()
        kw = self.entry.get_text().strip().lower()
        paths = recent.parse_recent(limit=max(200, core.conf_int('ui', 'limit', 80)))

        buckets = {}
        for p in paths:
            name = os.path.basename(p)
            d = os.path.dirname(p)
            if kw and kw not in name.lower() and kw not in d.lower():
                continue
            try:
                st = os.stat(p)
                mt, size = st.st_mtime, human_size(st.st_size)
            except OSError:
                mt, size = 0, '?'
            key, label = bucket_of(mt)
            buckets.setdefault(key, (label, []))[1].append((p, name, d, size, mt))

        total = 0
        self.rows = []
        for key in sorted(buckets):
            label, items = buckets[key]
            if not items:
                continue
            items.sort(key=lambda x: x[4], reverse=True)
            grow = self.store.append(None, [
                True, '', label, '', '', '%d 个' % len(items), '', '', '', 0.0])
            for (p, name, d, size, mt) in items:
                self.store.append(grow, [
                    False, type_icon(p), name, source_of(p), type_label(p),
                    size, d, when_str(mt), p, mt])
                self.rows.append(p)
            total += len(items)

        self.view.expand_all()
        if total:
            self.set_status('共 %d 个文档 · 双击复制，右键更多操作' % total)
        elif kw:
            self.set_status('没有匹配「%s」的文档' % kw)
        else:
            self.set_status('没读到最近文档。请先用 WPS 打开几个文件，再点刷新。')

    def on_search(self, *_):
        self.reload()

    # ------------------------------------------------ 选择 / 右键
    def on_select(self, sel):
        self.copy_btn.set_sensitive(self.current_path() is not None)

    def current_path(self):
        model, it = self.view.get_selection().get_selected()
        if it is None or model[it][C_ISGROUP]:
            return None
        return model[it][C_PATH]

    def on_button_press(self, view, event):
        if event.button != 3:
            return False
        pos = view.get_path_at_pos(int(event.x), int(event.y))
        if not pos:
            return False
        path = pos[0]
        model = view.get_model()
        it = model.get_iter(path)
        if model[it][C_ISGROUP]:
            return False
        view.get_selection().select_path(path)
        self.show_context_menu(event)
        return True

    def show_context_menu(self, event):
        menu = Gtk.Menu()
        entries = (
            ('打开', self.on_open_file),
            ('打开所在位置', self.on_open_dir),
            ('复制（放进剪贴板）', self.on_copy_clicked),
            ('复制文件路径', self.on_copy_path),
            (None, None),
            ('移到回收站', self.on_delete),
        )
        for label, cb in entries:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.connect('activate', lambda _w, cb=cb: cb())
            menu.append(mi)
        menu.show_all()
        try:
            menu.popup_at_pointer(event)
        except Exception:
            menu.popup(None, None, None, None, event.button, event.time)

    # ------------------------------------------------ 交互
    def on_activate(self, view, path, column):
        model = view.get_model()
        it = model.get_iter(path)
        if model[it][C_ISGROUP]:
            if view.row_expanded(path):
                view.collapse_row(path)
            else:
                view.expand_row(path, False)
            return
        self.copy_paths([model[it][C_PATH]])

    def on_copy_clicked(self, *_):
        p = self.current_path()
        if not p:
            self.set_status('请先在列表里选中一个文件')
            return
        self.copy_paths([p])

    def on_copy_path(self, *_):
        p = self.current_path()
        if not p:
            return
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(p, -1)
        clip.store()
        self.set_status('已复制路径：%s' % p)

    def on_open_file(self, *_):
        p = self.current_path()
        if not p:
            return
        try:
            Gtk.show_uri(None, GLib.filename_to_uri(p, None),
                         Gtk.get_current_event_time())
        except Exception as ex:
            self.set_status('打开失败：%s' % ex)

    def on_open_dir(self, *_):
        p = self.current_path()
        if not p:
            return
        try:
            Gtk.show_uri(None, GLib.filename_to_uri(os.path.dirname(p), None),
                         Gtk.get_current_event_time())
        except Exception as ex:
            self.set_status('打开文件夹失败：%s' % ex)

    def on_delete(self, *_):
        p = self.current_path()
        if not p:
            return
        name = os.path.basename(p)
        dlg = Gtk.MessageDialog(parent=self.win, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text='移到回收站？')
        dlg.format_secondary_text('「%s」\n可从系统回收站还原。' % name)
        dlg.add_button('取消', Gtk.ResponseType.CANCEL)
        dlg.add_button('移到回收站', Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        try:
            Gio.File.new_for_path(p).trash(None)
            self.set_status('已移到回收站：%s' % name)
            self.reload()
        except Exception as ex:
            self.set_status('删除失败：%s' % ex)

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
            return True
        if event.keyval == Gdk.KEY_Delete and self.view.is_focus():
            self.on_delete()
            return True
        return False

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
