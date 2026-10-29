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
import threading

os.environ.setdefault('GDK_BACKEND', 'x11')

from . import core  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import recent, service, settings as settings_mod  # noqa: E402

APP_ID = 'wps-clip-bridge'
APP_ICON = 'wps-file'

# 必须尽早设置进程名与程序类：
# WM_CLASS 由 GDK 在窗口 create 时用 g_get_prgname()/gdk_get_program_class()
# 生成，而这两个值默认取自 argv[0] 的 basename —— 我们是被 python3 启动的
# 脚本，于是 WM_CLASS 变成 "python3"，面板/任务栏就去找系统里现成的
# python3 图标来显示（ukui 主题里真有 python3.0.png），所以「托盘里是个
# Python 图标」。在这里（GTK 尚未创建任何窗口前）改成应用 ID 即可根治。
GLib.set_prgname(APP_ID)
try:
    Gdk.set_program_class(APP_ID)   # 3.16 起标记弃用，但仍是改 WM_CLASS 类名的唯一途径
except Exception:
    pass

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


# 图标可能被装到的地方：hicolor 各尺寸、pixmaps（部分面板/安装器只认这里）
_ICON_DIRS = (
    '/usr/share/icons/hicolor/scalable/apps',
    '/usr/share/icons/hicolor/512x512/apps',
    '/usr/share/icons/hicolor/256x256/apps',
    '/usr/share/icons/hicolor/128x128/apps',
    '/usr/share/icons/hicolor/64x64/apps',
    '/usr/share/icons/hicolor/48x48/apps',
    '/usr/share/pixmaps',
    '/usr/share/kylin-software-center/data/icons',
)


def _icon_files():
    """按优先级列出所有候选图标文件（含源码目录，便于未安装时调试）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for d in _ICON_DIRS:
        for name in (APP_ICON, APP_ID):
            for ext in ('.svg', '.png'):
                out.append(os.path.join(d, name + ext))
    out.append(os.path.join(here, '..', '..', 'data', 'wps-file.svg'))
    out.append(os.path.join(here, '..', '..', 'data', 'wps-file.png'))
    return out


def _icon_pixbufs(sizes=(16, 24, 32, 48, 64, 128)):
    """按多个小尺寸生成窗口图标 pixbuf 列表。

    ⚠ 关键坑：GDK 往 _NET_WM_ICON 写图标时有尺寸上限，实测 256×256 会
    写成空属性（xprop 里 _NET_WM_ICON 为空），面板取不到窗口图标，就会
    退回按 WM_CLASS 去图标主题里找 —— 于是显示成 python 图标。
    所以这里只用 ≤128 的尺寸，并一次给多个，让面板按需挑。
    """
    theme = None
    try:
        theme = Gtk.IconTheme.get_default()
    except Exception:
        pass
    out = []
    for sz in sizes:
        pb = None
        if theme is not None:
            for name in (APP_ICON, APP_ID):
                try:
                    if theme.has_icon(name):
                        pb = theme.load_icon(name, sz, 0)
                        if pb is not None:
                            break
                except Exception:
                    pb = None
        if pb is None:
            for p in _icon_files():
                if not os.path.exists(p):
                    continue
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(p, sz, sz, True)
                    break
                except Exception:
                    continue
        if pb is not None:
            out.append(pb)
    return out


def _load_app_icon(size=128):
    """取单个应用图标 pixbuf（尺寸不要超过 128，原因见 _icon_pixbufs）。"""
    pbs = _icon_pixbufs((size,))
    return pbs[0] if pbs else None


def _install_default_icon():
    """设置 GTK 全局默认窗口图标（含对话框、文件选择器）。

    只设主窗口的图标不够：消息框/文件选择器是独立窗口，未单独设置时面板
    会用 WM_CLASS 去主题里找，又退回 python 图标。设默认列表一劳永逸。
    """
    pbs = _icon_pixbufs()
    if pbs:
        try:
            Gtk.Window.set_default_icon_list(pbs)
        except Exception:
            pass
    return pbs


class App(object):
    def __init__(self):
        self.rows = []
        self.wake_mtime = core.mtime(core.gui_wake_path())
        self.tray = None
        self._ticks = 0
        self._copy_seq = 0          # 复制请求序号，用来作废过期回调
        self._copy_busy = False
        self._theme_dark = settings_mod.apply_theme()

        self.win = Gtk.Window(title='WPS 最近文档')
        self.win.set_default_size(1000, 640)
        self.win.get_style_context().add_class('wps-window')
        # WM_CLASS 显式指定（必须在 realize/show 之前）：
        # 桌面环境靠 WM_CLASS 或 StartupWMClass 把运行中的窗口关联到
        # .desktop，从而取到 Icon= 里的自定义图标。留空则退回 python3 图标。
        try:
            self.win.set_wmclass(APP_ID, APP_ID)
        except Exception:
            pass
        try:
            self.win.set_role(APP_ID)
        except Exception:
            pass
        _icon = _load_app_icon(128)
        if _icon is not None:
            # 多尺寸一起给：面板/任务栏会挑最合适的那个，避免缩放糊
            _pbs = _icon_pixbufs()
            try:
                self.win.set_icon_list(_pbs or [_icon])
            except Exception:
                self.win.set_icon(_icon)
        else:
            self.win.set_icon_name(APP_ICON)
        self.win.connect('destroy', Gtk.main_quit)
        self.win.connect('delete-event', self.on_close_window)
        self.win.connect('key-press-event', self.on_key)
        self._load_css()

        # 标题栏
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title('WPS 最近文档')
        hb.set_subtitle('双击复制 · 可拖到微信 · Ctrl/Shift 多选')
        self.win.set_titlebar(hb)

        self.settings_btn = Gtk.Button()
        self.settings_btn.set_image(Gtk.Image.new_from_icon_name(
            'emblem-system-symbolic', Gtk.IconSize.BUTTON))
        self.settings_btn.set_tooltip_text('设置（快捷键、主题、托盘、开机启动）')
        self.settings_btn.connect('clicked', lambda *a: self.open_settings())
        hb.pack_end(self.settings_btn)

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

        # 多选：Ctrl 点选、Shift 连选。分组标题不是文件，用 select_function
        # 挡在选中之外——否则 Ctrl+A 会把分组行也算进去，拖拽时更会带上空路径。
        sel = view.get_selection()
        sel.set_mode(Gtk.SelectionMode.MULTIPLE)
        sel.set_select_function(self._select_filter, None)
        sel.connect('changed', self.on_select)
        self.view = view
        self._setup_drag(view)

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
        statusbar = Gtk.Box(spacing=6)
        # 复制进度圈：只在后台忙的时候显示（set_no_show_all 防 show_all 把它带出来）
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        self.spinner.set_tooltip_text('后台正在处理')
        statusbar.pack_start(self.spinner, False, False, 0)
        statusbar.pack_start(self.status, True, True, 0)
        box.pack_start(statusbar, False, False, 0)

        self._setup_tray()
        self.reload()
        self.win.show_all()
        # 配了「启动时待在托盘」就不弹窗，但托盘必须存在，否则窗口无处唤回
        if core.conf_bool('ui', 'start_hidden', False) and self.tray_available():
            self.win.hide()
        GLib.timeout_add(WAKE_POLL_MS, self.poll_wake)
        # 顺手把后台服务预热起来：第一次复制不用再等它冷启动
        GLib.timeout_add(400, self._warmup)

    def _warmup(self):
        def work():
            service.warmup()
        threading.Thread(target=work, name='wps-warmup', daemon=True).start()
        return False

    # ------------------------------------------------ 托盘 / 设置
    def _setup_tray(self):
        try:
            from .tray import Tray
        except Exception as ex:
            core.log('托盘模块不可用: %r' % (ex,))
            return
        t = Tray(on_show=self.show_window,
                 on_copy=self.copy_latest,
                 on_settings=self.open_settings,
                 on_quit=self.quit_app)
        if t.start():
            self.tray = t

    def tray_available(self):
        return self.tray is not None and self.tray.available

    def quit_app(self, *_):
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        Gtk.main_quit()

    def show_window(self, *_):
        try:
            self.win.deiconify()
            self.win.show_all()
            self.win.present()
        except Exception:
            pass
        self.reload()

    def open_settings(self, *_):
        try:
            ok = settings_mod.open_settings(self.win)
        except Exception as ex:
            self.set_status('打开设置失败：%s' % ex)
            return
        if ok:
            self.reload()
            self.set_status('设置已保存，快捷键已生效')

    def copy_latest(self, *_):
        """托盘菜单 / 快捷键用：复制最近一个文档。"""
        try:
            paths = recent.parse_recent(limit=1)
        except Exception as ex:
            self.set_status('读取最近文档失败：%s' % ex)
            return
        if not paths:
            service.notify('没有最近文档可复制', '先用 WPS 打开一个文档')
            return
        self.copy_paths(paths[:1])

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
        # 重载后选择被清空，按钮状态要跟着复位（clear() 未必触发 changed）
        self.copy_btn.set_sensitive(False)
        self.copy_btn.set_label('复制选中文件')
        if total:
            self.set_status('共 %d 个文档 · 双击复制、拖到微信，Ctrl/Shift 可多选'
                            % total)
        elif kw:
            self.set_status('没有匹配「%s」的文档' % kw)
        else:
            self.set_status('没读到最近文档。请先用 WPS 打开几个文件，再点刷新。')

    def on_search(self, *_):
        self.reload()

    # ------------------------------------------------ 选择 / 右键
    def _select_filter(self, _sel, model, path, _selected, _data):
        """分组标题不允许被选中（多选与拖拽都靠这道闸）。"""
        try:
            it = model.get_iter(path)
        except Exception:
            return False
        return not model[it][C_ISGROUP]

    def selected_paths(self):
        """所有选中文件的路径，按列表顺序，已剔除分组行与重复项。"""
        model, rows = self.view.get_selection().get_selected_rows()
        out, seen = [], set()
        for p in rows:
            try:
                it = model.get_iter(p)
            except Exception:
                continue
            if model[it][C_ISGROUP]:
                continue
            path = model[it][C_PATH]
            if path and path not in seen:
                seen.add(path)
                out.append(path)
        return out

    def current_path(self):
        """主选中项（多选时取第一个），供「打开」「打开所在位置」等单文件操作。"""
        paths = self.selected_paths()
        return paths[0] if paths else None

    def on_select(self, sel):
        n = len(self.selected_paths())
        if self._copy_busy:
            # 复制进行中别把按钮重新点亮，等结果回来再恢复
            self.copy_btn.set_sensitive(False)
        else:
            self.copy_btn.set_sensitive(n > 0)
            self.copy_btn.set_label('复制选中文件' if n <= 1 else '复制 %d 个文件' % n)
        if n:
            self.set_status('已选 %d 个 · 双击复制，也可以直接拖到微信窗口' % n)

    # ------------------------------------------------ 拖拽到外部应用
    def _setup_drag(self, view):
        """把选中的文件拖出去：微信、桌面、文件管理器都能直接接收。"""
        try:
            view.drag_source_set(
                Gdk.ModifierType.BUTTON1_MASK,
                [Gtk.TargetEntry.new('text/uri-list', 0, 0)],
                Gdk.DragAction.COPY)
            view.connect('drag-data-get', self._on_drag_data_get)
            view.connect('drag-begin', self._on_drag_begin)
        except Exception as ex:
            core.log('拖拽源初始化失败: %r' % (ex,))

    def _on_drag_data_get(self, _view, _ctx, data, _info, _time):
        paths = self.selected_paths()
        if not paths:
            return
        data.set_uris([GLib.filename_to_uri(p, None) for p in paths])

    def _on_drag_begin(self, view, _ctx):
        paths = self.selected_paths()
        if not paths:
            return
        try:
            pb = Gtk.IconTheme.get_default().load_icon(type_icon(paths[0]), 48, 0)
            if pb is not None:
                view.drag_source_set_icon_pixbuf(pb)
        except Exception:
            pass

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
        sel = view.get_selection()
        # 右键点在已选中的行上 → 保留整组选择（可以对多选批量操作）；
        # 点在别处 → 只选这一行，跟文件管理器的习惯一致。
        if not sel.path_is_selected(path):
            sel.unselect_all()
            sel.select_path(path)
        self.show_context_menu(event)
        return True

    def show_context_menu(self, event):
        n = len(self.selected_paths())
        menu = Gtk.Menu()
        entries = (
            ('打开' if n <= 1 else '打开 %d 个文件' % n, self.on_open_file),
            ('打开所在位置', self.on_open_dir),
            ('复制（放进剪贴板）' if n <= 1 else '复制 %d 个文件' % n,
             self.on_copy_clicked),
            ('复制文件路径' if n <= 1 else '复制 %d 条路径' % n, self.on_copy_path),
            (None, None),
            ('移到回收站' if n <= 1 else '把 %d 个移到回收站' % n, self.on_delete),
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
        chosen = model[it][C_PATH]
        # 双击的那一行若在选中集里，就复制整组；否则只复制这一行
        paths = self.selected_paths()
        if chosen not in paths:
            paths = [chosen]
        self.copy_paths(paths)

    def on_copy_clicked(self, *_):
        paths = self.selected_paths()
        if not paths:
            self.set_status('请先在列表里选中文件')
            return
        self.copy_paths(paths)

    def on_copy_path(self, *_):
        paths = self.selected_paths()
        if not paths:
            return
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text('\n'.join(paths), -1)
        clip.store()
        if len(paths) == 1:
            self.set_status('已复制路径：%s' % paths[0])
        else:
            self.set_status('已复制 %d 条路径（每行一条）' % len(paths))

    def on_open_file(self, *_):
        paths = self.selected_paths()
        if not paths:
            return
        # 一次开太多会让桌面卡住，超出就只开前 8 个并说明
        limit = 8
        shown, failed = paths[:limit], 0
        for p in shown:
            try:
                Gtk.show_uri(None, GLib.filename_to_uri(p, None),
                             Gtk.get_current_event_time())
            except Exception:
                failed += 1
        if len(paths) > limit:
            self.set_status('已打开前 %d 个（共选中 %d 个）' % (limit, len(paths)))
        elif failed:
            self.set_status('有 %d 个文件打开失败' % failed)
        else:
            self.set_status('正在打开 %d 个文件…' % len(shown))

    def on_open_dir(self, *_):
        paths = self.selected_paths()
        if not paths:
            return
        dirs = []
        for p in paths:
            d = os.path.dirname(p)
            if d and d not in dirs:
                dirs.append(d)
        for d in dirs[:3]:
            try:
                Gtk.show_uri(None, GLib.filename_to_uri(d, None),
                             Gtk.get_current_event_time())
            except Exception as ex:
                self.set_status('打开文件夹失败：%s' % ex)
                return
        if len(dirs) > 3:
            self.set_status('已打开前 3 个文件夹（共 %d 个）' % len(dirs))
        elif len(dirs) > 1:
            self.set_status('已打开 %d 个文件夹' % len(dirs))

    def on_delete(self, *_):
        paths = self.selected_paths()
        if not paths:
            return
        if len(paths) == 1:
            text = '移到回收站？'
            sub = '「%s」\n可从系统回收站还原。' % os.path.basename(paths[0])
        else:
            text = '把这 %d 个文件移到回收站？' % len(paths)
            head = '\n'.join(os.path.basename(p) for p in paths[:5])
            if len(paths) > 5:
                head += '\n…'
            sub = '%s\n\n可从系统回收站还原。' % head
        dlg = Gtk.MessageDialog(parent=self.win, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=text)
        dlg.format_secondary_text(sub)
        dlg.add_button('取消', Gtk.ResponseType.CANCEL)
        dlg.add_button('移到回收站', Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        ok, failed = 0, 0
        for p in paths:
            try:
                Gio.File.new_for_path(p).trash(None)
                ok += 1
            except Exception as ex:
                failed += 1
                core.log('回收站失败 %s: %r' % (p, ex))
        if failed:
            self.set_status('已移到回收站 %d 个，%d 个失败' % (ok, failed))
        else:
            self.set_status('已移到回收站：%d 个' % ok)
        self.reload()

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

    def on_close_window(self, *_):
        """点关闭时：配了「收进托盘」且托盘可用就只隐藏。"""
        if core.conf_bool('ui', 'close_to_tray', True) and self.tray_available():
            self.win.hide()
            self.set_status('已收进托盘，快捷键随时唤回')
            return True
        self.quit_app()
        return True

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if core.conf_bool('ui', 'close_to_tray', True) and self.tray_available():
                self.win.hide()
            else:
                self.quit_app()
            return True
        focused = self.view.is_focus()
        if event.keyval == Gdk.KEY_Delete and focused:
            self.on_delete()
            return True
        # 焦点在列表上时接管 Ctrl+A / Ctrl+C（否则会落到搜索框或什么都不做）
        if focused and (event.state & Gdk.ModifierType.CONTROL_MASK):
            if event.keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.view.get_selection().select_all()
                return True
            if event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.on_copy_clicked()
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
        # 「跟随系统」要真的跟着走：约 5 秒看一次桌面明暗有没有变
        self._ticks += 1
        if self._ticks % 12 == 0 and core.conf('ui', 'theme', 'system').lower() == 'system':
            if settings_mod.system_dark() != self._theme_dark:
                self._theme_dark = settings_mod.apply_theme()
        return True

    def present(self):
        try:
            self.win.deiconify()
            self.win.present()
        except Exception:
            pass

    # ------------------------------------------------ 复制
    def copy_paths(self, paths):
        """把文件放进剪贴板。

        实际动作（必要时拉起后台、写入、等确认）最多要几秒，全部丢给
        service.copy_async 在后台线程里做，主线程只更新状态，界面不再假死。
        """
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            self.set_status('没有可复制的文件')
            return
        # 用序号作废过期的结果：连续点两次时，慢的那次的回调不会覆盖新结果
        self._copy_seq += 1
        seq = self._copy_seq
        self._copy_busy = True
        self._update_busy()
        self.set_status('正在复制…')

        def stage(text):
            GLib.idle_add(self._copy_stage, seq, text)

        def done(ok, msg, norm):
            GLib.idle_add(self._copy_done, seq, ok, msg, norm)

        service.copy_async(paths, done, stage)

    def _copy_stage(self, seq, text):
        """后台线程报进度（工作线程里发来，这里已在主线程）。"""
        if seq == self._copy_seq:
            self.set_status(text)
        return False

    def _copy_done(self, seq, ok, msg, paths):
        if seq != self._copy_seq:
            return False                    # 结果过期，已经有更新的复制在跑
        self._copy_busy = False
        self._update_busy()
        self._after_copy(ok, msg, paths)
        return False

    def _update_busy(self):
        busy = self._copy_busy
        n = len(self.selected_paths())
        if busy:
            label = '正在复制…'
        elif n <= 1:
            label = '复制选中文件'
        else:
            label = '复制 %d 个文件' % n
        self.copy_btn.set_label(label)
        self.copy_btn.set_sensitive((not busy) and n > 0)
        try:
            if busy:
                self.spinner.show()
                self.spinner.start()
            else:
                self.spinner.stop()
                self.spinner.hide()
        except Exception:
            pass

    def _after_copy(self, ok, msg, paths):
        if not ok:
            self.set_status('✘ %s' % msg)
            if not self.win.get_visible():
                service.notify('复制失败', msg)
            return
        brief = service.describe(paths)
        self.set_status('✔ 已复制：%s —— 切到桌面或微信按 Ctrl+V' % brief)
        if core.conf_bool('ui', 'notify_on_copy', True):
            service.notify('已复制 %s' % brief, '切到桌面或微信按 Ctrl+V 即可粘贴')
        if core.conf_bool('ui', 'hide_after_copy', True) and self.win.get_visible():
            GLib.timeout_add(220, self._hide_if_visible)

    def _hide_if_visible(self):
        if self.win.get_visible():
            self.win.hide()
        return False


def main(argv=None):
    # 进程名 / WM_CLASS 已在模块导入时设置好（见文件顶部），这里只需在所有
    # 窗口创建前装好全局默认图标，保证消息框、文件选择器这些独立窗口
    # 也显示自定义图标，而不是被面板按 WM_CLASS 兜底成 python 图标。
    _install_default_icon()
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
