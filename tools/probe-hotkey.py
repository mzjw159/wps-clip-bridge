#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""快捷键探针：在虚拟 X 里真实按键，验证「被系统占用」的组合键还能不能录。

这个 bug 的表现极安静：按下 Ctrl+Alt+T，设置窗口只收到 Ctrl、Alt 两个修饰键，
字母被桌面的 passive grab 截走，界面停在「请按下组合键…」，一句提示都没有。
所以用 xdotool 真发按键，再用另一个 X 连接占住几个组合键，把两种情况都跑一遍。

需要 X 服务器和 xdotool（没有显示器时用 Xvfb）::

    mkdir -p /tmp/.X11-unix && Xvfb :99 -screen 0 1280x820x24 &
    DISPLAY=:99 python3 tools/probe-hotkey.py

退出码：0 全过，1 有断言失败，2 环境不支持（没有 X / 没有 xdotool）。
"""
import os
import shutil
import subprocess
import sys
import time

H = '/tmp/wps-hotkey-probe'
shutil.rmtree(H, ignore_errors=True)
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

if not os.environ.get('DISPLAY'):
    print('没有 DISPLAY，跳过')
    sys.exit(2)
if not shutil.which('xdotool'):
    print('没有 xdotool，跳过')
    sys.exit(2)

import gi  # noqa: E402

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from wpsclip import hotkey, settings  # noqa: E402

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


# ---------------------------------------------------------------- 抢占方
# 等价的「桌面」：在 root 上被动抓几个组合键。Ctrl+Alt+T 开终端、Ctrl+Alt+C
# 是默认的唤起键，都是真实碰得到的例子。
comp = None
STEAL_KEYS = ('c', 't')


def start_competitor():
    global comp
    from Xlib import X, XK, display as xdisplay
    comp = xdisplay.Display()
    root = comp.screen().root
    for k in STEAL_KEYS:
        kc = comp.keysym_to_keycode(XK.string_to_keysym(k))
        root.grab_key(kc, X.ControlMask | X.Mod1Mask, True,
                      X.GrabModeAsync, X.GrabModeAsync)
    comp.sync()
    return comp is not None


def stop_competitor():
    if comp is None:
        return
    from Xlib import X
    try:
        comp.screen().root.ungrab_key(X.AnyKey, X.AnyModifier)
        comp.sync()
        comp.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 界面
seen = []
win = Gtk.Window()
win.set_default_size(460, 120)
btn = settings.HotkeyButton('', notify=lambda b: seen.append((b.combo, b.taken)))
win.add(btn)
win.show_all()
pump(0.3)
subprocess.run(['xdotool', 'windowfocus', '--sync',
                str(win.get_window().get_xid())], check=False)
pump(0.2)


def press(keys):
    subprocess.run(['xdotool', 'key', '--clearmodifiers', '--delay', '50', keys],
                   check=False)
    pump(0.5)


def record(keys):
    """点一次按钮 → 按下 keys → 返回 (录到的组合, 占用提示)。"""
    del seen[:]
    btn.combo = ''
    btn.taken = ''
    btn._start_capture()
    pump(0.15)
    grabbed = btn._grabbed
    press(keys)
    btn._stop_capture()
    taken = seen[-1][1] if seen else ''
    return btn.combo, taken, grabbed


# ---------------------------------------------------------------- [1] 常规
print('\n[1] 没有任何抢占时，各种组合都能录')
for keys, want in (('ctrl+c', '<Control>c'),
                   ('ctrl+alt+c', '<Control><Alt>c'),
                   ('ctrl+super+c', '<Control><Super>c')):
    got, taken, grabbed = record(keys)
    check(grabbed, '%s 期间抓到了键盘' % keys)
    check(got == want, '%s → %s' % (keys, want), '实际 %r' % got)
    check(taken == '', '%s 没被误报为占用' % keys, '实际 %r' % taken)

# ---------------------------------------------------------------- [2] 被抢占
print('\n[2] 组合键已被别的程序占住（桌面全局快捷键的常态）')
start_competitor()
pump(0.2)
for keys, want in (('ctrl+alt+c', '<Control><Alt>c'),
                   ('ctrl+alt+t', '<Control><Alt>t')):
    got, taken, _g = record(keys)
    check(got == want, '被占用也能录到 %s' % want, '实际 %r' % got)
    check(taken == settings.pretty_combo(want),
          '录完提示「已被占用」：%s' % settings.pretty_combo(want),
          '实际 %r' % taken)

# ---------------------------------------------------------------- [3] 语义
print('\n[3] Esc 取消不改值、Delete 清空、单独修饰键不生效')
btn.combo = '<Control><Alt>c'
btn._start_capture()
pump(0.1)
press('ctrl+alt+x')
btn._start_capture()
pump(0.1)
press('Escape')
check(btn.combo == '<Control><Alt>x', 'Esc 取消录制后保留原值', '实际 %r' % btn.combo)
check(not btn._grabbed, 'Esc 之后键盘抓取已释放')

btn._start_capture()
pump(0.1)
press('Delete')
check(btn.combo == '', 'Delete 清空', '实际 %r' % btn.combo)

btn.combo = '<Control><Alt>c'
btn._start_capture()
pump(0.1)
press('c')
check(btn.combo == '<Control><Alt>c', '单按 c 不会写成裸键', '实际 %r' % btn.combo)
check(btn._capturing, '单按 c 之后仍在等待下一个组合')
press('Escape')
check(not btn._grabbed, '取消后没有再占着键盘')

# ---------------------------------------------------------------- [4] 探测
print('\n[4] 占用探测本身')
check(hotkey.combo_conflicts('<Control><Alt>c') > 0, 'Ctrl+Alt+C 判定为被占用',
      '实际 %r' % hotkey.combo_conflicts('<Control><Alt>c'))
check(hotkey.combo_conflicts('<Control><Alt>k') == 0, 'Ctrl+Alt+K 判定为空闲',
      '实际 %r' % hotkey.combo_conflicts('<Control><Alt>k'))
check(hotkey.combo_conflicts('') is None, '空组合不探测')
check(hotkey.combo_conflicts('c') is None, '没有修饰键的组合不探测')

stop_competitor()
win.destroy()

print('\n%d 项通过，%d 项失败' % (len(ok), len(bad)))
if bad:
    for label in bad:
        print('  失败：%s' % label)
    sys.exit(1)
sys.exit(0)
