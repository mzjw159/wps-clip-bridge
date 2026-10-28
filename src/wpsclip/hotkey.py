#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""全局热键：按下组合键弹出「WPS 最近文档」窗口。

组合键可配置，`~/.config/wps-clip-bridge/config.ini`::

    [hotkey]
    enabled = true
    combo = <Control><Alt>c

采用 X11 的 ``grab_key``。WPS、微信、文件管理器都是 XWayland 客户端，
所以它们在前台时热键有效；Wayland 原生应用（如 GTK4 程序）前台时可能不响应，
这是 X11 键盘抓取的固有限制。
"""
import os
import subprocess
import sys
import time

from Xlib import X, XK, display

from . import core

MOD_MAP = {
    'control': X.ControlMask, 'ctrl': X.ControlMask,
    'alt': X.Mod1Mask, 'altgr': X.Mod5Mask,
    'shift': X.ShiftMask,
    'super': X.Mod4Mask, 'win': X.Mod4Mask, 'meta': X.Mod4Mask,
    'mod2': X.Mod2Mask, 'mod3': X.Mod3Mask, 'mod4': X.Mod4Mask,
}

DEBOUNCE = 0.7


def parse_combo(text):
    """``<Control><Alt>c`` -> (modmask, keysym-name)。

    也接受 ``Ctrl+Alt+C`` 这种写法。
    """
    text = (text or '').strip()
    if not text:
        return None

    mask = 0
    if '<' in text:
        rest = []
        i = 0
        while i < len(text):
            if text[i] == '<':
                j = text.find('>', i)
                if j < 0:
                    rest.append(text[i:])
                    break
                mask |= MOD_MAP.get(text[i + 1:j].strip().lower(), 0)
                i = j + 1
            else:
                rest.append(text[i])
                i += 1
        key = ''.join(rest).strip()
    else:
        parts = [p.strip() for p in text.split('+') if p.strip()]
        if not parts:
            return None
        key = parts[-1]
        for p in parts[:-1]:
            mask |= MOD_MAP.get(p.lower(), 0)

    if not key:
        return None
    return mask, key


def grab_all(d, mask, keyname):
    """抓取组合键的四种锁定状态，否则开大写/数字锁时热键会失效。"""
    root = d.screen().root
    keysym = XK.string_to_keysym(keyname)
    if not keysym:
        core.log('未知按键名: %r' % keyname)
        return []
    keycode = d.keysym_to_keycode(keysym)
    if not keycode:
        core.log('按键 %r 没有对应 keycode' % keyname)
        return []

    variants = [mask, mask | X.Mod2Mask, mask | X.LockMask,
                mask | X.Mod2Mask | X.LockMask]
    grabbed = []
    for m in variants:
        try:
            root.grab_key(keycode, m, True, X.GrabModeAsync, X.GrabModeAsync)
            grabbed.append((keycode, m))
        except Exception as ex:
            core.log('grab err %r' % (ex,))
    d.sync()
    return grabbed


def launch_gui():
    """已在运行就摸一下唤醒文件，让旧窗口跳到前台。"""
    if core.is_running('gui'):
        core.touch(core.gui_wake_path())
        core.log('gui already running, wake it')
        return
    from . import service
    try:
        out = open(os.path.join(core.state_dir(), 'gui.log'), 'ab')
        subprocess.Popen(service.exe_for('wps-copy-gui', 'wpsclip.gui'),
                         stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True)
        core.log('gui launched')
    except Exception as ex:
        core.log('launch err %r' % (ex,))


def main(argv=None):
    if not core.conf_bool('hotkey', 'enabled', True):
        core.log('热键已在配置中禁用，退出')
        return 0

    combo_text = core.conf('hotkey', 'combo', '<Control><Alt>c')
    parsed = parse_combo(combo_text)
    if not parsed:
        core.log('热键配置无法解析: %r' % combo_text)
        return 1
    mask, keyname = parsed

    si = core.SingleInstance('hotkey')
    if not si.acquire():
        core.log('另一个热键进程已在运行，本次退出')
        return 0

    d = display.Display(core.conf('bridge', 'display', '') or None)
    grabbed = grab_all(d, mask, keyname)
    core.log('热键 %s 已注册 (pid=%d, %d 组修饰状态)'
             % (combo_text, os.getpid(), len(grabbed)))
    if not grabbed:
        core.log('未能抓取到该组合键，可能已被其它程序占用')
        si.release()
        return 1

    last = 0.0
    root = d.screen().root
    try:
        while True:
            try:
                e = d.next_event()
            except Exception as ex:
                core.log('event err %r' % (ex,))
                time.sleep(0.5)
                continue
            if e.type == X.KeyPress:
                now = time.time()
                if now - last < DEBOUNCE:
                    continue
                last = now
                launch_gui()
            elif e.type == X.MappingNotify:
                # 键盘布局变化后重新抓取
                try:
                    root.ungrab_key(X.AnyKey, X.AnyModifier)
                except Exception:
                    pass
                for kc, m in grabbed:
                    try:
                        root.grab_key(kc, m, True, X.GrabModeAsync, X.GrabModeAsync)
                    except Exception:
                        pass
                d.sync()
                core.log('键盘映射变化，已重新抓取')
    except KeyboardInterrupt:
        pass
    finally:
        si.release()
    return 0


if __name__ == '__main__':
    sys.exit(main())
