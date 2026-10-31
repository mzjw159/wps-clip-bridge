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
- 列表按最近打开分组：今天 / 昨天 / 近 3 天 / 近 7 天 / 近 30 天 / 更早。
- 右键菜单：打开、打开所在位置、复制、复制路径、移到回收站。
"""
import datetime
import json
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

# ICCCM WM_STATE 的 IconicState：窗管把窗口收进了任务栏
ICCCM_ICONIC = 3

# TreeStore 列索引
# 列索引。C_BYTES 是给排序用的原始字节数 —— 「4.0 KB」是给人看的，比不了大小。
C_ISGROUP, C_ICON, C_NAME, C_SOURCE, C_TYPE, C_SIZE, C_DIR, C_WHEN, C_PATH, C_MTIME, C_BYTES = range(11)

# 分组顺序
_BUCKETS = ('今天', '昨天', '近 3 天', '近 7 天', '近 30 天', '更早')

_SOURCE_COLOR = {'云端': '#2f7bd6', '微信': '#07c160', '本地': '#8a8f98'}

# 列宽的可接受区间：太窄只剩一道缝（拖不出来），太宽会把别的列全挤没。
# 配置被手改坏了也在这两个数之间夹一下。
MIN_COL_WIDTH, MAX_COL_WIDTH = 40, 2000

# 列表一次最多画多少条。再多也只是让滚动条变长，找不到的还是找不到；
# 但搜索和筛选始终在全量上做，画不完的部分会在状态栏里说清还有多少。
PAGE_LIMIT = 200

# 记住宽度的列。最后一列（所在文件夹）不记：它是「吃掉剩余宽度」的那一列，
# 宽度是布局算出来的，不是用户调出来的 —— 记下来就等于把窗口当前大小
# 写死了，换个窄一点的窗口就只剩横向滚动条。
REMEMBER_COLS = ('name', 'source', 'type', 'size', 'mtime')

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


# 来源/类型的判断逻辑在 recent 里（命令行也要用），这里只留别名，界面内部
# 继续叫这两个名字，测试与旧代码都不受影响。
source_of = recent.source_of
type_label = recent.type_label


def highlight_markup(text, kw, tag_start='<span background="#ffe066" foreground="#1a1a1a">'):
    """把 text 里命中 kw 的每一段包上高亮，返回 Pango markup。

    搜索框一输字，列表里那一行的匹配位置要能一眼看到 —— 只把整行留下、
    让用户自己找是哪几个字，等于没筛。高亮用底色 + 深色字：深色主题下
    只改前景色会和背景糊在一起，底色 + 固定深色字两种主题都读得清。

    kw 可以含空格（多个词）：``hit_spans`` 会把每个词各自命中、重叠处合并，
    「季度 报告」两个词都亮，而不是只亮整串。

    先转义再包标签：文件名里带 ``<b>`` 时若不转义，整行 markup 解析失败，
    文字会整段消失（GTK 不会报错，只是不画）。
    """
    if not text:
        return ''
    # 分词后若一个词都没有（空串、纯空格），按无关键词处理
    spans = recent.hit_spans(text, recent.split_terms(kw))
    if not spans:
        return GLib.markup_escape_text(text)
    out, i = [], 0
    for start, end in spans:
        out.append(GLib.markup_escape_text(text[i:start]))
        out.append(tag_start + GLib.markup_escape_text(text[start:end]) + '</span>')
        i = end
    out.append(GLib.markup_escape_text(text[i:]))
    return ''.join(out)


def match_filter(path, type_sel='', src_sel=''):
    """筛选条件是否命中：类型与来源可以同时生效，空字符串表示「全部」。"""
    if type_sel and type_label(path) != type_sel:
        return False
    if src_sel and source_of(path) != src_sel:
        return False
    return True


def choice_index(choices, value):
    """下拉框里 value 对应的序号；第 0 项固定是「全部」，所以命中要 +1。

    配置里存的是中文取值（'表格'），版本升级后这个取值可能已经不在了 ——
    那时退回 0（全部），而不是让界面停在一个根本不存在的选项上。
    """
    try:
        return list(choices).index(value) + 1
    except (ValueError, TypeError):
        return 0


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


def tray_recent_entries(pinned, paths, limit=5, exists=os.path.isfile):
    """托盘菜单里「最近文档」那几行：置顶优先、去重、重名补上文件夹名。

    抽成模块级纯函数是为了能单独测：菜单项错一位用户就会点到别的文件，
    这种 bug 在界面上根本看不出来。
    """
    ordered = []
    for p in list(pinned) + list(paths):
        if p and p not in ordered and exists(p):
            ordered.append(p)
    out, seen = [], {}
    for p in ordered[:limit]:
        label = os.path.basename(p)
        n = seen.get(label, 0) + 1
        seen[label] = n
        if n > 1:
            # 重名时补上文件夹名，否则菜单里两行一模一样，点错了也不知道
            label = '%s（%s）' % (label, os.path.basename(os.path.dirname(p)))
        out.append((label, p))
    return out


class App(object):
    def __init__(self):
        self.rows = []
        # 当前搜索词（已转小写）。单元格渲染要拿它做高亮，所以存在实例上而不是
        # 每次去问搜索框 —— 重绘不只发生在 reload 里。
        self._kw = ''
        # 未过滤的完整列表：托盘菜单的「最近 5 个」用它，免得搜索时托盘跟着变
        self._all_paths = []
        # {路径: WPS 记录的打开时间}。列表顺序、按天分组、「最近打开」列都看它；
        # 没有记录的文件（兜底数据源、老配置）退回 st_mtime。
        self._opened = {}
        self.wake_mtime = core.mtime(core.gui_wake_path())
        self.tray = None
        self._ticks = 0
        self._copy_seq = 0          # 复制请求序号，用来作废过期回调
        self._copy_busy = False
        # 列宽：启动时从配置读回，拖动后防抖写回（见 _on_width_notify）。
        # _width_quiet 用来屏蔽「程序自己在设宽度」触发的保存。
        self._widths = {}
        self._width_quiet = True
        self._width_save_id = 0
        # 窗口尺寸一变，GTK 会重算各列宽度（最后一列自己填满），那一串
        # notify::width 是布局算出来的，不是用户拖的。用时间窗挡掉。
        self._layout_guard = 0
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

        # 筛选：类型 / 来源两个下拉，可以叠加（「表格」+「微信」就是微信来的表格）。
        # 选择会记进配置 —— 老是为同一份表格反复设筛选很烦。但「记住」也意味着
        # 下次打开看不到全部文件，所以过滤生效时工具栏上多一个「清除筛选」的 ×，
        # 状态栏和空列表提示也都会写明当前筛选，不至于让人以为文件丢了。
        self._quiet = False        # 程序改下拉时不触发保存/重载
        self.type_combo = Gtk.ComboBoxText()
        self.type_combo.append_text('全部类型')
        for _t in recent.TYPE_CHOICES:
            self.type_combo.append_text(_t)
        self.type_combo.set_active(choice_index(recent.TYPE_CHOICES,
                                                core.conf('ui', 'filter_type', '')))
        self.type_combo.set_tooltip_text('只看某一类文档（可和右边的来源叠加）')
        self.type_combo.connect('changed', self.on_filter_changed)
        toolbar.pack_start(self.type_combo, False, False, 0)

        self.src_combo = Gtk.ComboBoxText()
        self.src_combo.append_text('全部来源')
        for _s in recent.SOURCE_CHOICES:
            self.src_combo.append_text(_s)
        self.src_combo.set_active(choice_index(recent.SOURCE_CHOICES,
                                               core.conf('ui', 'filter_source', '')))
        self.src_combo.set_tooltip_text('只看云端 / 微信 / 本地来的文档')
        self.src_combo.connect('changed', self.on_filter_changed)
        toolbar.pack_start(self.src_combo, False, False, 0)

        self.clear_filter_btn = Gtk.Button()
        self.clear_filter_btn.set_image(Gtk.Image.new_from_icon_name(
            'edit-clear-symbolic', Gtk.IconSize.BUTTON))
        self.clear_filter_btn.set_tooltip_text('清除筛选，显示全部文档')
        self.clear_filter_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.clear_filter_btn.set_no_show_all(True)   # 只在有筛选时露出来
        self.clear_filter_btn.connect('clicked', self.on_clear_filter)
        toolbar.pack_start(self.clear_filter_btn, False, False, 0)

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
        self.store = Gtk.TreeStore(bool, str, str, str, str, str, str, str, str, float, float)
        view = Gtk.TreeView(model=self.store)
        view.set_headers_visible(True)
        view.set_enable_tree_lines(False)
        view.set_show_expanders(True)
        view.set_level_indentation(14)
        view.set_grid_lines(Gtk.TreeViewGridLines.HORIZONTAL)

        # 点表头排序。不用 GTK 自带的 sort_column_id：树里混着「分组行」和
        # 「文件行」，模型排序会把分组行也一起搬走，分组就散了。改成每次点击
        # 自己重排重填（reload），顺序完全可控。
        self._columns = {}
        self._sort = None          # None = 默认（按时间分组）；否则 (字段, 是否降序)
        # 列宽：配置里存的是 {"name": 320, ...}，坏值由 core.int_map 清掉
        self._widths = core.int_map('col_widths', REMEMBER_COLS,
                                    lo=MIN_COL_WIDTH, hi=MAX_COL_WIDTH)
        # 每列的默认宽度：用来判断「这次 notify 是不是真变了」，否则初始布局
        # 会把每个默认值都当成用户的选择写一遍
        self._default_widths = {}

        def _fit(col, field, default=None, min_width=0):
            """列可拖宽、可点排序；宽度记进配置，下次打开还是自己调好的样子。"""
            # 表头没有 tooltip API（TreeViewColumn 不实现 GtkTooltip），
            # 排序提示写在方向箭头和状态栏里
            col.set_resizable(True)
            if min_width:
                col.set_min_width(min_width)
            if default:
                self._default_widths[field] = default
            width = self._widths.get(field) or default
            if width:
                col.set_fixed_width(width)
            if field in REMEMBER_COLS:
                # 拖动分隔条会连发多次 notify，攒到松手后再写盘
                # （见 _on_width_notify）
                col.connect('notify::width', self._on_width_notify, field)
            col.set_clickable(True)
            col.connect('clicked', self.on_sort_clicked, field)
            self._columns[field] = col

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
        _fit(col, 'name', 320, min_width=160)
        view.append_column(col)

        # 来源
        src_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('来源')
        col.pack_start(src_cell, True)
        col.set_cell_data_func(src_cell, self._source_func)
        _fit(col, 'source', 64, min_width=48)
        view.append_column(col)

        # 类型
        type_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('类型')
        col.pack_start(type_cell, True)
        col.set_cell_data_func(type_cell, self._plain_func(C_TYPE))
        _fit(col, 'type', 64, min_width=48)
        view.append_column(col)

        # 大小
        size_cell = Gtk.CellRendererText()
        size_cell.set_property('xalign', 1.0)
        col = Gtk.TreeViewColumn('大小')
        col.pack_start(size_cell, True)
        col.set_cell_data_func(size_cell, self._plain_func(C_SIZE))
        _fit(col, 'size', 80, min_width=64)
        view.append_column(col)

        # 最近打开（不是文件修改时间：打开老文档不会改 mtime，用户要看的是
        # 「我什么时候用它」，而排序和分组都按这个）
        when_cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('最近打开')
        col.pack_start(when_cell, True)
        col.set_cell_data_func(when_cell, self._plain_func(C_WHEN))
        _fit(col, 'mtime', 105, min_width=84)
        view.append_column(col)

        # 所在文件夹
        dir_cell = Gtk.CellRendererText()
        dir_cell.set_property('ellipsize', 3)
        col = Gtk.TreeViewColumn('所在文件夹')
        col.pack_start(dir_cell, True)
        col.set_cell_data_func(dir_cell, self._dir_func)
        # 文件夹列没存过宽度时不设 fixed_width：让它吃掉窗口剩下的宽度
        _fit(col, 'dir', min_width=160)
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
        # 必须在 show_all 之前接上：首次分配尺寸也算「窗口变了」
        self.win.connect('size-allocate', self._on_win_size)
        self.win.show_all()
        # 布局稳定之后才允许记录列宽：前面 set_fixed_width / show_all 都会
        # 触发 notify::width，那些是程序自己在摆位，不是用户拖出来的
        self._width_quiet = False
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
                 on_quit=self.quit_app,
                 on_copy_path=self.copy_paths,
                 on_recent=self._tray_recent)
        if t.start():
            self.tray = t

    # 托盘菜单里最多列几条最近文档。5 条是「够用又不至于把菜单撑长」的经验值：
    # 再多就比打开窗口还慢了。
    TRAY_RECENT = 5

    def _tray_recent(self):
        """托盘菜单里的「最近文档」几行，返回 [(短标签, 路径), ...]。

        置顶的排前面（托盘菜单也是用户最常用的入口，置顶在那儿更该生效）。
        不重新解析配置文件：用窗口上次 reload 的结果，D-Bus 调用里读磁盘会
        把面板的菜单卡住。
        """
        paths = list(self._all_paths)
        if not paths:
            try:
                paths = recent.parse_recent(limit=self.TRAY_RECENT * 2)
            except Exception:
                paths = []
        return tray_recent_entries(core.pinned_paths(), paths, self.TRAY_RECENT)

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
        self._drop_xdpy()
        Gtk.main_quit()

    def show_window(self, *_):
        # 先把窗口「显示」再「提到前面」：deiconify 只对已映射的窗口有效，
        # 而「启动即隐藏」时窗口连 X 窗口都还没创建，只 deiconify 是叫不出来的。
        #
        # 「是否还在任务栏里」必须在 show()/deiconify() 之前问：GDK 只要收到
        # 我们自己发的 deiconify 就会把图标化标记抹掉，事后再问必然是 False。
        was_iconic = self._is_iconified()
        try:
            self.win.show()
            self.win.deiconify()
            self.win.show_all()
        except Exception as ex:
            # 这里以前是 pass —— 托盘点了没反应也无从查起
            core.log('显示窗口失败: %r' % (ex,))
        self._kick_raise(iconic=was_iconic)
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
            # 匹配到的字加底色：只把行留下、让人自己找是哪几个字，等于没筛
            cell.set_property('markup',
                              highlight_markup(model[it][C_NAME] or '', self._kw))

    def _dir_func(self, _col, cell, model, it, _data):
        if model[it][C_ISGROUP]:
            cell.set_property('markup', '')
            return
        # 搜索也匹配文件夹，这里同样高亮 —— 命中在目录名上时名称列是干净的，
        # 不高亮的话看不出这行为什么被留下
        cell.set_property('markup',
                          highlight_markup(model[it][C_DIR] or '', self._kw))

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

    def _add_file(self, parent, p, mt=None, size=None, nbytes=None, when=None):
        """往列表里插一行文件（分组行不走这里）。

        ``when`` 是这一行要显示的「最近打开」，默认等于文件修改时间。两者
        分开传，是因为打开一份老文档不会改 mtime —— 只显示 mtime 的话，用户
        会看到「今天」组里那一行写着三个月前，对不上。
        """
        if mt is None or nbytes is None:
            try:
                st = os.stat(p)
                mt, nbytes = st.st_mtime, st.st_size
                size = human_size(nbytes)
            except OSError:
                mt, size, nbytes = 0, '?', None
        self.store.append(parent, [
            False, type_icon(p), os.path.basename(p), source_of(p),
            type_label(p), size, os.path.dirname(p),
            when_str(mt if when is None else when), p, mt,
            float(nbytes or 0)])
        self.rows.append(p)
        return mt

    def _type_filter(self):
        """下拉里选的类型；第 0 项是「全部类型」，返回空串表示不筛。"""
        try:
            idx = self.type_combo.get_active()
            return '' if idx <= 0 else (self.type_combo.get_active_text() or '')
        except Exception:
            return ''

    def _src_filter(self):
        try:
            idx = self.src_combo.get_active()
            return '' if idx <= 0 else (self.src_combo.get_active_text() or '')
        except Exception:
            return ''

    def _filter_label(self):
        """「表格 / 微信」这样的一行字，状态栏和空列表提示共用。"""
        return ' / '.join(x for x in (self._type_filter(), self._src_filter()) if x)

    def _save_filters(self):
        """把当前筛选写进配置，下次打开窗口还是这个视角。"""
        try:
            core.save_conf({('ui', 'filter_type'): self._type_filter(),
                            ('ui', 'filter_source'): self._src_filter()})
        except Exception as ex:
            # 存不下只影响「记不住」，不该让筛选本身失效
            core.log('保存筛选条件失败: %r' % (ex,))

    def on_filter_changed(self, *_):
        if self._quiet:      # 程序自己改下拉（恢复配置、清除筛选）不算用户操作
            return
        self._save_filters()
        self.reload()

    def on_clear_filter(self, *_):
        """一键回到「全部」：筛选会被记住，得给条明路回去。"""
        self._quiet = True
        try:
            self.type_combo.set_active(0)
            self.src_combo.set_active(0)
        finally:
            self._quiet = False
        self._save_filters()
        self.reload()

    # ------------------------------------------------ 排序
    def on_sort_clicked(self, _col, field):
        """表头三态：升序 → 降序 → 回到默认（按最近打开分组）。

        不做成 GTK 的 sort_column_id：模型排序会把分组行一起搬走，
        「今天/昨天」就散了。这里改成自己重排重填，顺序完全可控。
        """
        if not self._sort or self._sort[0] != field:
            self._sort = (field, False)
        elif not self._sort[1]:
            self._sort = (field, True)
        else:
            self._sort = None
        self._sync_sort_titles()
        self.reload()

    def _sync_sort_titles(self):
        """排序方向写进表头文字（'大小 ↓'）。

        不用 set_sort_indicator：那个要求列有 sort_column_id，一旦设了，
        点表头会走 TreeView 的模型排序，分组行也会被重排。
        """
        for field, col in self._columns.items():
            title = recent.SORT_LABELS[field]
            if self._sort and self._sort[0] == field:
                title += ' ↓' if self._sort[1] else ' ↑'
            col.set_title(title)

    def _on_width_notify(self, col, _pspec, field):
        """列宽变了：攒一会儿再写配置。

        拖分隔条时 notify 会连着一串发出来（几乎每像素一次），每次都写盘就是
        拿磁盘换一条拖动动画。这里只记最新值，600ms 没新动作才真正落盘。

        麻烦在于「列宽变了」不等于「用户拖了」：窗口一改大小，GTK 会重算所有
        列（最后一列自己吃掉剩余宽度），那些 notify 也长得一模一样。不挡住
        的话，每次开关窗口都会把布局结果当成用户的选择存下来，最后列宽就
        锁死在某个尺寸上。所以窗口尺寸变化后的一小段时间里一律不记。
        """
        if self._width_quiet or GLib.get_monotonic_time() < self._layout_guard:
            return
        width = col.get_width()
        if width < MIN_COL_WIDTH:
            # 还没 realize、或者窗口藏起来时宽度是 1，这种值存进去会让下次
            # 打开时所有列都变成一条缝
            return
        if self._widths.get(field) == width:
            return
        # 初始布局会为每列各发一次 notify，宽度还是默认值 —— 那不是用户改的
        if field not in self._widths and width == self._default_widths.get(field):
            return
        self._widths[field] = width
        if self._width_save_id:
            try:
                GLib.source_remove(self._width_save_id)
            except Exception:
                pass
        self._width_save_id = GLib.timeout_add(600, self._save_widths)

    def _on_win_size(self, _win, _alloc):
        """窗口尺寸变了：接下来的列宽变化是布局算的，30 毫秒内不记。

        用 30ms 这种很短的窗口就够：GTK 的重新布局在 size-allocate 的同一轮
        主循环里就做完了，而人手拖一次分隔条要几百毫秒。
        """
        self._layout_guard = GLib.get_monotonic_time() + 30000

    def _save_widths(self):
        self._width_save_id = 0
        try:
            core.save_conf({('ui', 'col_widths'):
                            json.dumps(self._widths, sort_keys=True)})
        except Exception as ex:
            core.log('保存列宽失败: %r' % (ex,))
        return False

    def reload(self):
        self.store.clear()
        # 搜索框原文留着：单元格高亮要按原文分词，转小写交给 highlight_markup
        raw_kw = self.entry.get_text().strip()
        terms = recent.split_terms(raw_kw)
        self._kw = raw_kw
        tsel, ssel = self._type_filter(), self._src_filter()
        limit = max(PAGE_LIMIT, core.conf_int('ui', 'limit', 80))
        # 一次读全量：搜索和筛选必须覆盖所有文档 —— 只在前 200 条里搜，
        # 用户会以为「明明有的文件不见了」。画多少条由 limit 另行控制。
        #
        # 用 collect_meta 而不是 collect：每一行要带上 WPS 记录的打开时间，
        # 列表顺序、按天分组、「最近打开」列都按它算 —— 只打开不改内容的老
        # 文档也能像 WPS 那样挪到最前面。
        all_rows, _stats = recent.collect_meta()
        all_paths = [p for p, _ts in all_rows]
        self._opened = {p: ts for p, ts in all_rows if ts}
        # 托盘菜单的「最近 5 个」用完整列表：搜索和筛选都只是「看」的姿势，
        # 不该把「合上窗口也能随手复制最近文档」这条路径改窄。
        self._all_paths = list(all_paths)

        def keep(p):
            # 多个词是「并且」：每个词都要在名字或目录里出现
            return match_filter(p, tsel, ssel) and recent.match_terms(p, terms)

        def when_of(p, mt):
            """这一行显示的「最近打开」：WPS 有记录就用它，没有才退回 mtime。"""
            return self._opened.get(p) or mt or 0

        def entries_of(items):
            """[(路径, 字节数, 文件修改时间)]；stat 失败的大小记 None，显示成 ?。"""
            out = []
            for p in items:
                try:
                    st = os.stat(p)
                    out.append((p, st.st_size, st.st_mtime))
                except OSError:
                    out.append((p, None, 0))
            return out

        self.rows = []
        total = 0

        # 置顶：单独一组放最前，其它分组里不再重复出现。
        # 文件已经不在了就先不显示（U 盘没插、目录被挪走都会这样），但也不
        # 从配置里删 —— 换回来还应该在。
        pinned_all = core.pinned_paths()
        pinned_set = set(pinned_all)
        pinned_entries = entries_of(
            [p for p in pinned_all if os.path.isfile(p) and keep(p)])
        # 置顶的不占分页名额（它们单独成组，永远在最前）
        rest_kept = [p for p in all_paths if p not in pinned_set and keep(p)]
        shown, hidden = recent.paginate(rest_kept, limit)
        rest_entries = entries_of(shown)

        sort_label = ''
        if self._sort:
            # 手动排序时不按天分组：两套顺序会打架 —— 点了「大小」却看到
            # 「今天」那组里塞着去年的大文件，只会更糊涂。
            field, rev = self._sort
            sort_label = '按%s%s' % (recent.SORT_LABELS.get(field, field),
                                    '降序' if rev else '升序')
            pinned_entries = recent.sort_entries(pinned_entries, field, rev,
                                                 opened=self._opened)
            rest_entries = recent.sort_entries(rest_entries, field, rev,
                                               opened=self._opened)
            groups = [('按%s排序' % recent.SORT_LABELS.get(field, field),
                       rest_entries)]
        else:
            buckets = {}
            for p, nbytes, mt in rest_entries:
                # 分组也看打开时间：打开一份三个月前的老文档，它今天就该在
                # 「今天」组里 —— 这正是「最近文档」应有的样子。
                key, label = bucket_of(when_of(p, mt))
                buckets.setdefault(key, (label, []))[1].append((p, nbytes, mt))
            groups = []
            for key in sorted(buckets):
                label, items = buckets[key]
                if items:
                    # 组内也是「有打开记录的在先」（和 collect 的档位一致），
                    # 档内再按时间倒序。不加档位的话，一个刚被别的软件改过、
                    # 但 WPS 没打开过的文件会压过刚打开过的那份。
                    # 档位写成「有记录=1」而不是 0：这里 reverse=True，写 0 会被
                    # 翻到后面去。
                    items.sort(key=lambda x: (1 if self._opened.get(x[0]) else 0,
                                              when_of(x[0], x[2]), x[2]),
                               reverse=True)
                    groups.append((label, items))

        def add_group(label, items):
            grow = self.store.append(None, [
                True, '', label, '', '', '%d 个' % len(items), '', '', '', 0.0, 0.0])
            for (p, nbytes, mt) in items:
                size = '?' if nbytes is None else human_size(nbytes)
                self._add_file(grow, p, mt, size, nbytes, when=when_of(p, mt))

        if pinned_entries:
            add_group('置顶', pinned_entries)
            total += len(pinned_entries)
        for label, items in groups:
            if not items:
                continue
            add_group(label, items)
            total += len(items)

        self.view.expand_all()
        # 重载后选择被清空，按钮状态要跟着复位（clear() 未必触发 changed）
        self.copy_btn.set_sensitive(False)
        self.copy_btn.set_label('复制选中文件')
        flt = self._filter_label()
        # 有筛选时把「清除」露出来：筛选是记住的，找不到这个按钮就没法一眼还原
        try:
            self.clear_filter_btn.set_visible(bool(flt))
        except Exception:
            pass
        if total:
            npin = len(pinned_entries)
            extra = '（%d 个置顶）' % npin if npin else ''
            if flt:
                extra += ' · 筛选：%s' % flt
            if sort_label:
                extra += ' · %s' % sort_label
            if hidden:
                # 只画前 limit 条：滚动条再长也翻不到 200 条以后，与其让人
                # 一直往下滚找，不如直说还差多少、以及怎么找到剩下的
                extra += ' · 另有 %d 个更早的未列出' % hidden
            self.set_status('共 %d 个文档%s · 双击或回车复制，↑↓ 选择，Ctrl/Shift 多选'
                            % (total, extra))
            if hidden:
                self.status.set_tooltip_text(
                    '列表一次最多显示前 %d 个（按最近打开）。\n'
                    '想找更早的文件，直接在搜索框输文件名 —— 搜索覆盖全部 %d 个，\n'
                    '不受这个上限影响。' % (limit, len(all_paths)))
            else:
                self.status.set_tooltip_text(None)
            self._show_empty_hint('')
        elif raw_kw or flt:
            # 搜不到有两种原因：关键词不对，或者筛选条件把它挡掉了。
            # 只说「没有匹配」的话，用户不会想到是上面那两个下拉干的。
            if raw_kw and flt:
                msg = '没有同时符合「%s」和筛选（%s）的文档' % (raw_kw, flt)
            elif raw_kw:
                msg = '没有匹配「%s」的文档' % raw_kw
            else:
                msg = '筛选（%s）下没有文档' % flt
            hint = msg + '\n换个关键词'
            if len(terms) > 1:
                hint += '（多个词空格分开，要同时命中）'
            if flt:
                hint += '，或点右边 × 清除筛选'
            hint += '，或清空搜索框'
            self.set_status(msg)
            self.status.set_tooltip_text(None)
            self._show_empty_hint(hint)
        else:
            # 列表空的三种原因（没装 WPS / 格式变了 / 文件被删）要分清，
            # 否则用户只看到一句「没读到最近文档」，不知道该找谁。
            info = recent.diagnose()
            self.set_status('没读到最近文档 —— 原因见中间的提示')
            self.status.set_tooltip_text(self._diagnose_detail(info))
            self._show_empty_hint('%s\n\n详细自检：wps-copy --doctor' % info['hint'])

        # 列表变了，托盘菜单里的「最近 5 个」要跟着变
        try:
            if getattr(self, 'tray', None) is not None:
                self.tray.refresh_menu()
        except Exception as ex:
            core.log('刷新托盘菜单失败: %r' % (ex,))

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
        cur = self.current_path()
        pinned = bool(cur) and core.is_pinned(cur)
        if n <= 1:
            pin_label = '取消置顶' if pinned else '置顶到最前'
        else:
            pin_label = '取消置顶' if pinned else '把这 %d 个置顶' % n
        menu = Gtk.Menu()
        entries = (
            ('打开' if n <= 1 else '打开 %d 个文件' % n, self.on_open_file),
            ('打开所在位置', self.on_open_dir),
            ('复制（放进剪贴板）' if n <= 1 else '复制 %d 个文件' % n,
             self.on_copy_clicked),
            ('复制文件路径' if n <= 1 else '复制 %d 条路径' % n, self.on_copy_path),
            (None, None),
            (pin_label, self.on_toggle_pin),
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

    def on_toggle_pin(self, *_):
        """置顶 / 取消置顶。多选时以第一项的状态决定整批怎么切。"""
        paths = self.selected_paths()
        if not paths:
            return
        want = not core.is_pinned(paths[0])
        cur = core.pinned_paths()
        if want:
            # 倒序插入，多选时保持列表里的原有顺序
            for p in reversed(paths):
                if p not in cur:
                    cur.insert(0, p)
        else:
            drop = set(paths)
            cur = [p for p in cur if p not in drop]
        core.set_pinned(cur)
        self.reload()
        if want:
            self.set_status('已置顶 %d 个（在列表最上方）' % len(paths) if len(paths) > 1
                            else '已置顶：%s' % os.path.basename(paths[0]))
        else:
            self.set_status('已取消置顶 %d 个' % len(paths) if len(paths) > 1
                            else '已取消置顶：%s' % os.path.basename(paths[0]))

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

    def _file_rows(self):
        """列表里所有可选行（跳过分组标题）的 TreePath，按显示顺序。"""
        out = []

        def walk(it):
            while it is not None:
                if self.store[it][C_ISGROUP]:
                    walk(self.store.iter_children(it))
                else:
                    out.append(self.store.get_path(it))
                it = self.store.iter_next(it)

        walk(self.store.get_iter_first())
        return out

    def _select_rows(self, rows):
        sel = self.view.get_selection()
        sel.unselect_all()
        for p in rows:
            sel.select_path(p)
        if rows:
            self.view.set_cursor(rows[0], None, False)
            self.view.scroll_to_cell(rows[0], None, False, 0.5, 0)

    def _nav_key(self, key):
        """上下键。返回 True 表示「这一下我处理了」。

        两种情况 GTK 自己处理不好：
        * 列表里一个都没选中时按 ↓ —— 它只搬 cursor 不选中，看着像没反应；
        * 焦点在搜索框里按 ↓ —— 事件根本到不了列表，用户搜完想直接选第一项。
        已经有选中且焦点在列表上时返回 False，交给 GTK 按原样走（多选、
        Shift 连选那些行为保持不变）。
        """
        down = key in (Gdk.KEY_Down, Gdk.KEY_KP_Down)
        focused = self.view.is_focus()
        if focused and self.selected_paths():
            return False
        rows = self._file_rows()
        if not rows:
            return False
        self.view.grab_focus()
        sel = self.selected_paths()
        if sel:
            # 焦点从搜索框切回来：停在原来的选中项上，别把用户的选择弄丢
            for rp in rows:
                if self.store[self.store.get_iter(rp)][C_PATH] == sel[0]:
                    self.view.set_cursor(rp, None, False)
                    self.view.scroll_to_cell(rp, None, False, 0.5, 0)
                    return True
        self._select_rows([rows[0] if down else rows[-1]])
        return True

    def on_key(self, widget, event):
        key = event.keyval
        # Ctrl+F 回到搜索框：手在键盘上时不用去够鼠标
        if key in (Gdk.KEY_f, Gdk.KEY_F) and (event.state & Gdk.ModifierType.CONTROL_MASK):
            self.entry.grab_focus()
            self.entry.select_region(0, -1)      # 连按两次可以直接改写
            return True
        if key == Gdk.KEY_Escape:
            # 正在搜索时 Esc 先清空搜索框（搜索框里的通行做法），再按一次才收窗口。
            # 否则「搜错了想退出来」会连着把窗口也关掉。
            try:
                if self.entry.is_focus() and self.entry.get_text():
                    self.entry.set_text('')
                    return True
            except Exception:
                pass
            if core.conf_bool('ui', 'close_to_tray', True) and self.tray_available():
                self._save_geometry()
                self.win.hide()
            else:
                self.quit_app()
            return True
        focused = self.view.is_focus()
        no_mod = not (event.state & (Gdk.ModifierType.CONTROL_MASK
                                     | Gdk.ModifierType.SHIFT_MASK
                                     | Gdk.ModifierType.MOD1_MASK))
        if no_mod and key in (Gdk.KEY_Down, Gdk.KEY_KP_Down,
                              Gdk.KEY_Up, Gdk.KEY_KP_Up):
            return self._nav_key(key)
        # 回车 = 双击。在搜索框里回车则复制第一条结果 —— 「搜完就粘」不用换手
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if not (focused or self.entry.is_focus()):
                return False
            paths = self.selected_paths()
            if not paths:
                rows = self._file_rows()
                if rows and self.entry.is_focus():
                    self._select_rows(rows[:1])
                    paths = self.selected_paths()
            if not paths:
                return False
            self.copy_paths(paths)
            return True
        if focused and (event.state & Gdk.ModifierType.CONTROL_MASK):
            if event.keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.view.get_selection().select_all()
                return True
            if event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.on_copy_clicked()
                return True
        if event.keyval == Gdk.KEY_Delete and focused:
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
        # 「跟随系统」要真的跟着走：约 5 秒看一次桌面明暗有没有变
        self._ticks += 1
        if self._ticks % 12 == 0 and core.conf('ui', 'theme', 'system').lower() == 'system':
            if settings_mod.system_dark() != self._theme_dark:
                self._theme_dark = settings_mod.apply_theme()
        return True

    def present(self):
        # 热键唤醒走这里：同样要分清「藏在后台」和「缩在任务栏」两种状态
        was_iconic = self._is_iconified()
        try:
            self.win.show()
            self.win.deiconify()
        except Exception:
            pass
        self._kick_raise(iconic=was_iconic)

    # 最小化之后补几拍、每拍隔多久（毫秒）：窗口恢复是窗管的异步动作，
    # 一拍打不通不代表不行；连打大半秒足够，又不会把界面拖住。
    RAISE_STEPS = 8
    RAISE_INTERVAL = 70
    # 第几拍还不行就整窗重挂（见 _remap_window）；放在靠后，先让激活请求多试
    RAISE_REMAP_STEP = 6

    def _kick_raise(self, step=0, iconic=None):
        """把窗口从「最小化 / 被挡住」里捞出来。

        以前这里直接 ``deiconify() + present()``：窗口只是藏起来时没问题，
        可最小化到任务栏之后往往就唤不出来 —— ``deiconify()`` 只是给窗管发了
        个异步请求，窗口真正取消图标化之前，紧跟着的 ``present()`` 会被窗管
        当成对「还在任务栏里的窗口」的无效请求丢掉；再加上「防抢焦点」策略，
        干脆连请求都不理。所以这里按拍重试，每拍补一条 EWMH 激活请求，最后
        还有整窗重挂兜底，直到窗管确认窗口已经正常。
        """
        if step > 0 or iconic is None:
            # 以窗管的说法为准：真出来了就收手，别把窗口一直往上抢
            iconic = self._is_iconified()
        try:
            if iconic and step == self.RAISE_REMAP_STEP:
                self._remap_window()
            if iconic:
                self.win.deiconify()
                # GTK 这条路可能被窗管忽略，再直接问一次窗管
                self._x11_activate()
            self.win.present_with_time(Gdk.CURRENT_TIME)
            w = self.win.get_window()
            if w is not None:
                w.raise_()
        except Exception as ex:
            core.log('提升窗口失败: %r' % (ex,))
        if iconic and step + 1 < self.RAISE_STEPS:
            GLib.timeout_add(self.RAISE_INTERVAL, self._kick_raise, step + 1, True)

    def _is_iconified(self):
        """窗口是否还缩在任务栏里（图标化）。

        以 ICCCM 的 ``WM_STATE`` 为准，而不是 GDK 缓存的状态：只要我们自己
        调过 ``deiconify()``，GDK 就会把这个标记抹掉，问它永远得到 False。
        """
        state = self._wm_state()
        if state is not None:
            return state == ICCCM_ICONIC
        try:
            w = self.win.get_window()
            return bool(w is not None and w.get_state() & Gdk.WindowState.ICONIFIED)
        except Exception:
            return False

    def _xdpy(self):
        """复用一个 X 连接：一次唤回要问好几遍窗管状态。"""
        d = getattr(self, '_xdpy_cache', None)
        if d is None:
            from Xlib import display
            d = display.Display()
            self._xdpy_cache = d
        return d

    def _drop_xdpy(self):
        d = getattr(self, '_xdpy_cache', None)
        self._xdpy_cache = None
        try:
            if d is not None:
                d.close()
        except Exception:
            pass

    def _wm_state(self):
        """读窗管给的窗口状态：1=正常 3=图标化；读不到返回 None。"""
        try:
            w = self.win.get_window()
            if w is None:
                return None
            d = self._xdpy()
            win = d.create_resource_object('window', w.get_xid())
            atom = d.intern_atom('WM_STATE')
            prop = win.get_full_property(atom, atom)
            if prop is None or not len(prop.value):
                return None
            return int(prop.value[0])
        except Exception:
            self._drop_xdpy()
            return None

    def _x11_activate(self):
        """绕开 GTK，直接给窗管发 EWMH ``_NET_ACTIVE_WINDOW``。

        source 填 2（分页器）：这一档最不容易被「防抢焦点」策略挡下来。
        效果等同于用户从任务栏点了窗口 —— 取消最小化、切桌面、提到最前。
        """
        try:
            from Xlib import X
            from Xlib.protocol import event as xev
        except Exception:
            return False
        try:
            w = self.win.get_window()
            if w is None:
                return False
            d = self._xdpy()
            root = d.screen().root
            ev = xev.ClientMessage(
                window=d.create_resource_object('window', w.get_xid()),
                client_type=d.intern_atom('_NET_ACTIVE_WINDOW'),
                data=(32, [2, X.CurrentTime, 0, 0, 0]))
            root.send_event(
                ev, event_mask=(X.SubstructureRedirectMask |
                                X.SubstructureNotifyMask))
            d.flush()
            return True
        except Exception as ex:
            self._drop_xdpy()
            core.log('X11 激活窗口失败: %r' % (ex,))
            return False

    def _remap_window(self):
        """兜底：把窗口整扇收回去再挂出来。

        对多数窗管来说，「取消最小化」可以不理，但「新窗口要映射」总得管；
        这条就是赌后者。实测 KWin 系连 MapRequest 也不理（所以真正管用的还是
        上面那条激活请求），留着它是为了别的窗管——反正这时窗口本来就缩在
        任务栏里，用户看不到中间那一下。尺寸位置先记下，重新映射后原样摆回。
        """
        try:
            w = self.win.get_window()
            g = w.get_geometry() if w is not None else None
        except Exception:
            g = None
        try:
            self.win.hide()
            # 让 withdraw 真的发到 X 上：同一次迭代里 hide+show 会被合并成
            # 一次映射请求，窗管根本不认为这是新窗口
            for _ in range(8):
                if not Gtk.events_pending():
                    break
                Gtk.main_iteration_do(False)
            self.win.show_all()
            if g is not None:
                self.win.resize(g.width, g.height)
                self.win.move(g.x, g.y)
                core.log('窗口唤不出，已整窗重挂一次（%dx%d @%d,%d）'
                         % (g.width, g.height, g.x, g.y))
            else:
                core.log('窗口唤不出，已整窗重挂一次')
        except Exception as ex:
            core.log('整窗重挂失败: %r' % (ex,))

    # ------------------------------------------------ 复制
    def copy_paths(self, paths):
        """把文件放进剪贴板。

        实际动作（必要时拉起后台、写入、等确认）最多要几秒，全部丢给
        service.copy_async 在后台线程里做，主线程只更新状态，界面不再假死。

        参数可以是单个路径，也可以是路径列表 —— 托盘菜单传的是单个字符串，
        界面里传的是列表，这里统一（见 :func:`core.as_paths`）。
        """
        paths = [p for p in core.as_paths(paths) if os.path.isfile(p)]
        if not paths:
            self.set_status('没有可复制的文件')
            # 托盘点到已被删除/移走的文件时窗口是藏着的，状态栏看不见 ——
            # 「点了没反应」比报错更让人困惑，所以这种情况必须弹通知。
            if not self.win.get_visible():
                service.notify('没有可复制的文件', '它可能已被移动或删除')
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
