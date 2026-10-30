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

# 窗口四周用于拖拽缩放的热区宽度（像素）
GRIP = 5

# TreeStore 列索引
C_ISGROUP, C_ICON, C_NAME, C_SOURCE, C_TYPE, C_SIZE, C_DIR, C_WHEN, C_PATH, C_MTIME = range(10)

# 分组顺序
_BUCKETS = ('今天', '昨天', '近 3 天', '近 7 天', '近 30 天', '更早')

_SOURCE_COLOR = {'云端': '#2f7bd6', '微信': '#07c160', '本地': '#8a8f98'}

def clamp_window_pos(x, y, w, h, screen_w, screen_h, keep=120):
    """把记住的窗口坐标拉回可见范围。

    换显示器、改分辨率、拔掉外接屏之后，上次的位置可能整个落到屏幕外，
    直接 move() 过去就是「窗口打开了但看不见」。这里保证至少 keep×keep
    的一块留在屏内。
    """
    try:
        x = int(x)
        y = int(y)
    except (TypeError, ValueError):
        return None
    max_x = max(0, int(screen_w) - keep)
    max_y = max(0, int(screen_h) - keep)
    return (min(max(x, 0), max_x), min(max(y, 0), max_y))


CSS = b"""
.toolbar { padding: 10px 12px 2px 12px; }
.bottom-bar { padding: 8px 2px 0 2px; }
.status-label { opacity: 0.72; font-size: 92%; }
.empty-hint { opacity: 0.62; font-size: 95%; }
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

        # 用系统原生标题栏（不再自绘 HeaderBar）。
        # 自绘的那套是客户端装饰：这台机器的 KWin 不认 CSD 边框，鼠标按在
        # 窗口边上只会移动窗口、拉不动大小，程序自己加热区也没用 —— KWin 在
        # 事件到达之前就把边缘那十几像素吃掉了。交给窗管画边框，拖动缩放、
        # 双击最大化这些行为就都跟系统里其它程序一致。
        self.win.set_title('WPS 最近文档')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(12)
        # 窗口边框交给窗管画了，但这套 KWin 的边框是 0 宽（_NET_FRAME_EXTENTS
        # 左右下都是 0），鼠标没处可抓。自己再铺一圈 5px 的热区，按下去用
        # _NET_WM_MOVERESIZE 让窗管来缩放，等于把「可抓的边」补回来。
        self.grip_grid = Gtk.Grid()
        box.set_hexpand(True)
        box.set_vexpand(True)
        self.grip_grid.attach(box, 1, 1, 1, 1)
        self._install_resize_grips(self.grip_grid)
        self.win.add(self.grip_grid)

        # 工具栏：搜索框 + 两个按钮（原来的标题栏按钮挪到这里）
        toolbar = Gtk.Box(spacing=8)
        toolbar.get_style_context().add_class('toolbar')
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text('搜索文件名或文件夹…')
        self.entry.connect('search-changed', self.on_search)
        toolbar.pack_start(self.entry, True, True, 0)

        self.refresh_btn = Gtk.Button()
        self.refresh_btn.set_image(Gtk.Image.new_from_icon_name(
            'view-refresh-symbolic', Gtk.IconSize.BUTTON))
        self.refresh_btn.set_tooltip_text('刷新列表')
        self.refresh_btn.connect('clicked', lambda *a: self.reload())
        toolbar.pack_start(self.refresh_btn, False, False, 0)

        self.settings_btn = Gtk.Button()
        self.settings_btn.set_image(Gtk.Image.new_from_icon_name(
            'emblem-system-symbolic', Gtk.IconSize.BUTTON))
        self.settings_btn.set_tooltip_text('设置（快捷键、主题、托盘、开机启动）')
        self.settings_btn.connect('clicked', lambda *a: self.open_settings())
        toolbar.pack_start(self.settings_btn, False, False, 0)
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

        # 空列表时在中间给一句人话（放状态栏里会被 ellipsize 截掉，看不清）
        self.empty_hint = Gtk.Label()
        self.empty_hint.set_line_wrap(True)
        self.empty_hint.set_max_width_chars(44)
        self.empty_hint.set_justify(Gtk.Justification.CENTER)
        self.empty_hint.set_xalign(0.5)
        self.empty_hint.get_style_context().add_class('empty-hint')
        self.empty_hint.set_no_show_all(True)
        overlay = Gtk.Overlay()
        overlay.add(scroll)
        overlay.add_overlay(self.empty_hint)
        overlay.set_overlay_pass_through(self.empty_hint, True)
        box.pack_start(overlay, True, True, 0)

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
        # 先摆回上次关闭时的位置和大小，再显示（顺序反了会被默认尺寸覆盖）
        self._restore_geometry()
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

    # ------------------------------------------------ 窗口几何
    def _install_resize_grips(self, grid):
        """四边四角各放一条透明热区，按下去交给窗管缩放。

        KWin 给这个窗口的边框是 0 宽，鼠标按在边上等于按在桌面，
        根本抓不住。自己在窗口内侧铺 5px 的热区，用 begin_resize_drag
        把动作转给窗管，拖动边缘/四角就能改大小了。
        """
        # (列, 行, 边缘, 光标)
        specs = (
            (0, 0, Gdk.WindowEdge.NORTH_WEST, 'nw-resize'),
            (1, 0, Gdk.WindowEdge.NORTH, 'n-resize'),
            (2, 0, Gdk.WindowEdge.NORTH_EAST, 'ne-resize'),
            (0, 1, Gdk.WindowEdge.WEST, 'w-resize'),
            (2, 1, Gdk.WindowEdge.EAST, 'e-resize'),
            (0, 2, Gdk.WindowEdge.SOUTH_WEST, 'sw-resize'),
            (1, 2, Gdk.WindowEdge.SOUTH, 's-resize'),
            (2, 2, Gdk.WindowEdge.SOUTH_EAST, 'se-resize'),
        )
        self._grips = []
        for col, row, edge, cursor_name in specs:
            grip = Gtk.EventBox()
            # 必须有自己的 GdkWindow 才收得到按键：set_visible_window(False)
            # 会连窗口一起去掉（get_has_window() → False），点了没反应。
            grip.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
            grip.set_size_request(GRIP, GRIP)
            grip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                            | Gdk.EventMask.BUTTON_RELEASE_MASK
                            | Gdk.EventMask.POINTER_MOTION_MASK)
            grip.connect('button-press-event', self._grip_press, edge)
            grip.connect('motion-notify-event', self._grip_motion)
            grip.connect('button-release-event', self._grip_release)
            grip.connect('realize', self._grip_realize, cursor_name)
            grid.attach(grip, col, row, 1, 1)
            self._grips.append(grip)

    @staticmethod
    def _grip_realize(grip, cursor_name):
        try:
            cur = Gdk.Cursor.new_from_name(Gdk.Display.get_default(), cursor_name)
            grip.get_window().set_cursor(cur)
        except Exception:
            pass

    def _grip_press(self, grip, event, edge):
        """按在热区上：记下起点，之后按鼠标位移改窗口大小。

        不用 begin_resize_drag 交给窗管 —— 这台机器的 KWin 收下请求却不动
        窗口；也不用 seat.grab —— 按下按钮时 X 本来就会给这个窗口隐式抓取，
        motion / release 都会照常送到抓手上来。
        """
        if event.button != 1:
            return False
        try:
            x, y = self.win.get_position()
            w, h = self.win.get_size()
        except Exception:
            return False
        self._drag = {
            'edge': edge,
            'x0': int(event.x_root),
            'y0': int(event.y_root),
            'geo': (x, y, w, h),
        }
        return True

    def _grip_motion(self, _grip, event):
        drag = getattr(self, '_drag', None)
        if not drag or not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        x, y, w, h = drag['geo']
        dx = int(event.x_root) - drag['x0']
        dy = int(event.y_root) - drag['y0']
        nick = drag['edge'].value_nick
        min_w, min_h = 520, 360
        if 'west' in nick:
            nw = max(min_w, w - dx)
            x = x + (w - nw)
            w = nw
        elif 'east' in nick:
            w = max(min_w, w + dx)
        if 'north' in nick:
            nh = max(min_h, h - dy)
            y = y + (h - nh)
            h = nh
        elif 'south' in nick:
            h = max(min_h, h + dy)
        try:
            self.win.move(x, y)
            self.win.resize(w, h)
        except Exception as ex:
            core.log('拖动缩放失败: %r' % (ex,))
        return True

    def _grip_release(self, _grip, event):
        if getattr(self, '_drag', None):
            self._drag = None
            return True
        return False

    def _save_geometry(self):
        """记住关闭时的位置和大小（下一次点托盘直接回到原处）。"""
        try:
            if self.win.get_window() is None:
                return
            state = self.win.get_window().get_state()
            if state & Gdk.WindowState.MAXIMIZED:
                # 最大化时记的是屏幕尺寸，还原出来就成了「全屏窗口」
                return
            x, y = self.win.get_position()
            w, h = self.win.get_size()
            core.save_conf({
                ('ui', 'window_x'): x,
                ('ui', 'window_y'): y,
                ('ui', 'window_w'): w,
                ('ui', 'window_h'): h,
            })
        except Exception as ex:
            core.log('保存窗口位置失败: %r' % (ex,))

    def _restore_geometry(self):
        x = core.conf_int('ui', 'window_x', -1)
        y = core.conf_int('ui', 'window_y', -1)
        w = core.conf_int('ui', 'window_w', 0)
        h = core.conf_int('ui', 'window_h', 0)
        try:
            if w >= 480 and h >= 320:
                self.win.resize(w, h)
        except Exception:
            pass
        if x < 0 or y < 0:
            return
        try:
            screen = Gdk.Screen.get_default()
            pos = clamp_window_pos(x, y, w, h,
                                   screen.get_width(), screen.get_height())
        except Exception:
            pos = None
        if pos:
            try:
                self.win.move(pos[0], pos[1])
            except Exception:
                pass

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

    def reload_tray(self):
        """按当前配置重建托盘（设置里换了「托盘图标样式」时调用）。"""
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        self.tray = None
        self._setup_tray()
        ok = self.tray_available()
        if ok:
            core.log('托盘已重建：%s' % self.tray.backend)
        elif not self.win.get_visible():
            # 新后端不可用，而窗口是藏起来的 —— 否则用户就彻底找不到程序了
            self.show_window()
            self.set_status('托盘不可用，已把窗口显示出来')
        return ok

    def quit_app(self, *_):
        self._save_geometry()
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        Gtk.main_quit()

    def show_window(self, *_):
        # 先把窗口「显示」再「提到前面」：deiconify 只对已映射的窗口有效，
        # 而「启动即隐藏」时窗口连 X 窗口都还没创建，只 deiconify 是叫不出来的。
        try:
            self.win.show()
            self.win.deiconify()
            self.win.show_all()
            self.win.present()
            # 有的窗管不认 present()，再抢一次焦点，失败也无所谓
            try:
                self.win.grab_focus()
            except Exception:
                pass
        except Exception as ex:
            # 这里以前是 pass —— 托盘点了没反应也无从查起
            core.log('显示窗口失败: %r' % (ex,))
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
    @staticmethod
    def _diagnose_detail(info):
        """空列表时的多行明细（挂在状态栏 tooltip 上）。"""
        lines = [info.get('hint', '')]
        files = info.get('conf_files') or []
        if files:
            for p, size in files:
                lines.append('配置：%s（%d 字节）' % (p, size))
        else:
            lines.append('配置：没找到 %s' % recent.CONF)
        lines.append('解析：结构化 %d 条 / 通用扫描 %d 条 / 共 %d 条，'
                     '其中 %d 个文件存在'
                     % (info.get('structured', 0), info.get('scanned', 0),
                        info.get('total', 0), info.get('existing', 0)))
        if info.get('fallback'):
            lines.append('兜底数据源：%s（%d 条）'
                         % (info['fallback'], info.get('fallback_hits', 0)))
        lines.append('详细自检：wps-copy --doctor')
        return '\n'.join(lines)

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
            self.status.set_tooltip_text(None)
            self._show_empty_hint('')
        elif kw:
            self.set_status('没有匹配「%s」的文档' % kw)
            self.status.set_tooltip_text(None)
            self._show_empty_hint('没有匹配「%s」的文档\n换个关键词，或清空搜索框' % kw)
        else:
            # 列表空的三种原因（没装 WPS / 格式变了 / 文件被删）要分清，
            # 否则用户只看到一句「没读到最近文档」，不知道该找谁。
            info = recent.diagnose()
            self.set_status('没读到最近文档 —— 原因见中间的提示')
            self.status.set_tooltip_text(self._diagnose_detail(info))
            self._show_empty_hint('%s\n\n详细自检：wps-copy --doctor' % info['hint'])

    def _show_empty_hint(self, text):
        try:
            if text:
                self.empty_hint.set_text(text)
                self.empty_hint.show()
            else:
                self.empty_hint.hide()
        except Exception:
            pass

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
            self._save_geometry()
            self.win.hide()
            self.set_status('已收进托盘，快捷键随时唤回')
            return True
        self.quit_app()
        return True

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if core.conf_bool('ui', 'close_to_tray', True) and self.tray_available():
                self._save_geometry()
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
