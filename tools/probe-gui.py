#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""界面探针：在虚拟 X 里真开一个窗口，逐项断言界面行为。

单测覆盖不到的地方全靠它 —— 列表分组、搜索高亮、排序三态、列宽记忆、超量
分页、复制链路，这些错了的表现都很「安静」：不会崩，只是结果不对。

需要 X 服务器（没有显示器时用 Xvfb）::

    mkdir -p /tmp/.X11-unix && Xvfb :99 -screen 0 1280x820x24 &
    DISPLAY=:99 python3 tools/probe-gui.py

全部通过时退出码 0，截图落在 build/probe-gui.png。
"""
import os
import shutil
import sys
import time

H = '/tmp/wps-probe-home'
shutil.rmtree(H, ignore_errors=True)
os.makedirs(H + '/.config/Kingsoft')
os.makedirs(H + '/.config/wps-clip-bridge')
os.makedirs(H + '/run')
os.chmod(H + '/run', 0o700)
os.environ['HOME'] = H
os.environ['XDG_CONFIG_HOME'] = H + '/.config'
os.environ['XDG_RUNTIME_DIR'] = H + '/run'
os.environ['XDG_STATE_HOME'] = H + '/state'
os.environ['GDK_BACKEND'] = 'x11'

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'src'))

import gi  # noqa: E402

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from wpsclip import core, gui  # noqa: E402

_BUIlD = os.path.join(_ROOT, 'build')
os.makedirs(_BUIlD, exist_ok=True)
SHOT = os.path.join(_BUIlD, 'probe-gui.png')

ok, bad = [], []


def check(cond, label, detail=''):
    if cond:
        ok.append(label)
        print('  ✔ %s' % label)
    else:
        bad.append(label)
        print('  ✘ %s%s' % (label, ('  ← %s' % detail) if detail else ''))


def pump(seconds=0.45):
    deadline = time.time() + seconds
    while time.time() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


# ---------------------------------------------------------------- 假数据
# 260 个文档：超过 PAGE_LIMIT(200)，用来验证「只画前 200 条」的提示，
# 以及搜索能不能命中第 200 条之后的文件（以前只在前 200 条里搜）
docs = []
now = time.time()
for i in range(260):
    p = os.path.join(H, 'doc-%03d.docx' % i)
    with open(p, 'w') as f:
        f.write('x' * 1024)
    mt = now - i * 3600
    os.utime(p, (mt, mt))
    docs.append(p)
with open(H + '/.config/Kingsoft/Office.conf', 'w') as f:
    for i, p in enumerate(docs, 1):
        # 单反斜杠：真实 Office.conf 就是这么写的。以前这里写成双反斜杠，
        # 结构化解析其实一条都没匹配上，全靠「通用扫描」兜住 —— 那一层就
        # 等于从来没被测过。
        f.write('wps\\RecentFiles\\files\\%d\\path=%s\n' % (i, p))

print('假数据：%d 个文档' % len(docs))


def rows(app):
    """列表里的文件名（按显示顺序，跳过分组行）。"""
    out = []

    def walk(it):
        while it is not None:
            if not app.store[it][gui.C_ISGROUP]:
                out.append(app.store[it][gui.C_NAME])
            walk(app.store.iter_children(it))
            it = app.store.iter_next(it)

    walk(app.store.get_iter_first())
    return out


print('\n[1] 超量分页提示')
app = gui.App()
pump(0.8)
names = rows(app)
status = app.status.get_text()
print('  状态栏: %s' % status)
check(len(names) == gui.PAGE_LIMIT, '只画前 %d 条' % gui.PAGE_LIMIT, len(names))
check('另有 60 个更早的未列出' in status, '状态栏说明还剩多少', status)
check(app.status.get_tooltip_text() is not None, '悬停能看到完整解释')

print('\n[2] 搜索覆盖全部（不受分页上限影响）')
# 第 256 个文件在 200 条之外，只在前 200 里搜是搜不到的
app.entry.set_text('doc-255')
pump(0.7)
names = rows(app)
check(names == ['doc-255.docx'], '能搜到第 256 个', names)
check('另有' not in app.status.get_text(), '结果只有一个时不再提「还有多少」',
      app.status.get_text())
app.entry.set_text('')
pump(0.7)

print('\n[3] 列宽记忆')
col = app._columns['name']
before = col.get_width()
print('  名称列初始宽度: %d' % before)
check(before == 320, '先用默认宽度', before)
# 拖动分隔条在 GTK 里就是改 fixed_width，这里模拟同一动作
col.set_fixed_width(420)
pump(0.9)                       # 600ms 防抖之后才落盘
saved = core.int_map('col_widths', ('name', 'size', 'dir'))
print('  配置里: %s' % saved)
check(saved.get('name') == 420, '宽度写进配置', saved)
check(col.get_width() == 420, '这一列本身也变了', col.get_width())
# 只拖一列不该把别的列也写进去（dir 是自动填满的，不能当成用户拖过）
check('dir' not in saved, '没拖过的列不写进配置', saved)

print('\n[4] 重开窗口后恢复列宽')
app.win.destroy()
pump(0.3)
app2 = gui.App()
pump(0.8)
got = app2._columns['name'].get_width()
print('  新窗口名称列宽度: %d' % got)
check(got == 420, '记住的宽度被恢复', got)

print('\n[5] 托盘里的最近文档点了要真复制')
# 直接调 copy_paths 并把 service.copy_async 换成探针版本：
# 字符串必须原样变成 [路径]，不能被按字符拆开
calls = []


class _Spy(object):
    def __call__(self, paths, on_done, on_stage=None):
        calls.append(list(paths))
        return None


real = gui.service.copy_async
gui.service.copy_async = _Spy()
try:
    app2.copy_paths(docs[0])            # 托盘的调用方式：单个字符串
    app2.copy_paths([docs[1], docs[2]])
finally:
    gui.service.copy_async = real
check(calls and calls[0] == [docs[0]], '单个路径不被拆成字符', calls)
check(calls[1:] == [[docs[1], docs[2]]], '列表照旧', calls)

# 真跑一次：应该走到「正在复制…」而不是「没有可复制的文件」
app2.copy_paths(docs[0])
pump(0.4)
state = app2.status.get_text()
print('  复制后状态: %s' % state)
check('没有可复制的文件' not in state, '真的开始复制了', state)

print('\n[6] 走一遍托盘菜单的点击回调（端到端）')
from wpsclip import tray as tray_mod  # noqa: E402

notices = []
real_notify = gui.service.notify
gui.service.notify = lambda s, b='': notices.append((s, b))
try:
    # 不 start()，只借 _copy_path 这一段：托盘三种后端点的都是它
    t = tray_mod.Tray(on_show=lambda: None, on_copy=lambda: None,
                      on_settings=lambda: None, on_quit=lambda: None,
                      on_copy_path=app2.copy_paths, on_recent=lambda: [])
    t._copy_path(docs[0])
    pump(1.2)
finally:
    gui.service.notify = real_notify
state = app2.status.get_text()
print('  状态: %s' % state)
print('  通知: %s' % notices)
check('没有可复制的文件' not in state, '托盘点击触发了复制', state)
check(any('已复制' in s for s, _b in notices) or '已复制' in state,
      '复制成功后有反馈（通知或状态栏）', (notices, state))

print('\n[7] 打开时间决定顺序（文件没改也算最近）')
# 用户报的 bug：打开一份排在后面的老文档、不修改内容，WPS 那边排到第一，
# 这边还在原位 —— 因为旧实现按文件 mtime 排。现在按 WPS 的 Sequence 时间戳。
old = docs[250]                     # 250 小时前改过（「近 30 天」那组）
seq_ts = int(time.time() - 30)
with open(H + '/.config/Kingsoft/Office.conf', 'a') as f:
    f.write('wps\\RecentFiles\\Sequence\\%s=%d\n'
            % (old.lstrip('/').replace('/', '\\'), seq_ts))
print('  给 %s 补一条「刚刚打开」的记录' % os.path.basename(old))
app2.reload()
pump(0.8)
names = rows(app2)
check(names and names[0] == 'doc-250.docx', '刚打开的老文档排到第一', names[:3])

first_group = app2.store[app2.store.get_iter_first()][gui.C_NAME]
check(first_group == '今天', '它进了「今天」组（按打开时间分组）', first_group)

# 这一行显示的时间应该是打开时间（今天的 HH:MM），不是三个月前的日期
shown = None
group_it = app2.store.get_iter_first()
while group_it is not None:
    child = app2.store.iter_children(group_it)
    while child is not None:
        if app2.store[child][gui.C_NAME] == 'doc-250.docx':
            shown = app2.store[child][gui.C_WHEN]
        child = app2.store.iter_next(child)
    group_it = app2.store.iter_next(group_it)
print('  「最近打开」列: %s' % shown)
check(bool(shown) and len(shown) == 5 and shown[2] == ':',
      '列里显示今天的时刻，不是旧日期', shown)
check(app2._columns['mtime'].get_title() == '最近打开',
      '列名跟着语义走', app2._columns['mtime'].get_title())

# 点表头排序时也用同一个尺度（否则点一下顺序就变回按 mtime）
app2.on_sort_clicked(app2._columns['mtime'], 'mtime')
pump(0.6)
if app2._sort and not app2._sort[1]:
    app2.on_sort_clicked(app2._columns['mtime'], 'mtime')   # 切到降序
pump(0.6)
print('  排序状态: %s' % (app2._sort,))
check(rows(app2)[0] == 'doc-250.docx', '按「最近打开」降序仍是它', rows(app2)[:3])
app2.on_sort_clicked(app2._columns['mtime'], 'mtime')       # 回到默认分组
pump(0.6)

print('\n[8] 截图')
app2.win.show_all()
app2.win.present()
app2.win.resize(1180, 760)
pump(1.2)
win = app2.win.get_window()
if win is None:
    print('  窗口还没 realize')
else:
    from gi.repository import Gdk
    print('  viewable=%s size=%dx%d' % (win.is_viewable(), win.get_width(),
                                        win.get_height()))
    pb = Gdk.pixbuf_get_from_window(win, 0, 0, win.get_width(), win.get_height())
    if pb is None:
        print('  截图失败（窗口不可见）')
    else:
        pb.savev(SHOT, 'png', [], [])
        print('  截图: %s' % SHOT)

print('\n通过 %d 项，失败 %d 项' % (len(ok), len(bad)))
if bad:
    for label in bad:
        print('  失败: %s' % label)
sys.exit(1 if bad else 0)
