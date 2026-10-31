#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""全局热键：一个键弹出「WPS 最近文档」窗口，另一个键直接复制最近文档。

两组热键都可配置，`~/.config/wps-clip-bridge/config.ini`::

    [hotkey]
    enabled = true
    combo = <Control><Alt>c          # 唤起窗口
    copy_enabled = true
    copy_combo = <Control><Shift>c   # 复制最近一个文档，不弹窗

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
    """抓取组合键的四种锁定状态，否则开大写/数字锁时热键会失效。

    返回 ``(grabbed, conflicts)``：conflicts 是抓取时被别的程序占用的修饰状态
    数量。X 的 GrabKey 冲突不会抛异常，只会异步返回 BadAccess —— 必须临时插一个
    错误处理器再 sync 才看得见，否则「别人的机器上 Ctrl+Alt+C 早就被占了」时
    热键会一声不响地失效。
    """
    root = d.screen().root
    keysym = XK.string_to_keysym(keyname)
    if not keysym:
        core.log('未知按键名: %r' % keyname)
        return [], 0
    keycode = d.keysym_to_keycode(keysym)
    if not keycode:
        core.log('按键 %r 没有对应 keycode' % keyname)
        return [], 0

    variants = [mask, mask | X.Mod2Mask, mask | X.LockMask,
                mask | X.Mod2Mask | X.LockMask]
    errors = []
    prev = None
    if hasattr(d, 'set_error_handler'):
        try:
            prev = d.set_error_handler(lambda e, r: errors.append(e))
        except Exception:
            prev = None
    grabbed = []
    try:
        for m in variants:
            try:
                root.grab_key(keycode, m, True, X.GrabModeAsync, X.GrabModeAsync)
                grabbed.append((keycode, m))
            except Exception as ex:
                core.log('grab err %r' % (ex,))
        d.sync()
    finally:
        if prev is not None:
            try:
                d.set_error_handler(prev)
            except Exception:
                pass
    return grabbed, len(errors)


def combo_conflicts(text):
    """探测组合键是不是已经被别的程序占了，返回被占的修饰状态组数（0~4）。

    ``0`` 表示四组修饰状态（大写锁/数字锁的四种组合）都抓得到，也就是这个
    组合空着；``None`` 表示压根解析不了或者连不上 X，调用方别拿它报错。

    为什么要单独探测：桌面的全局快捷键（Ctrl+Alt+T 开终端、Ctrl+Alt+X 截图）
    在 X 层是 passive grab，我们的守护进程启动时抢不过它，只会静默失效。
    设置窗口在用户刚录完键的时候探一次，就能当场告诉他「这个组合被占了」，
    而不是保存完再弹一句「没抢到」。

    探测期间确实会短暂抓住这个键，抓完立刻全部放开，返回前就释放干净。
    """
    parsed = parse_combo(text)
    if not parsed or not parsed[0]:
        return None
    mask, keyname = parsed
    try:
        d = display.Display(core.conf('bridge', 'display', '') or None)
    except Exception as ex:
        core.log('探测快捷键占用时连不上 X: %r' % (ex,))
        return None
    try:
        grabbed, bad = grab_all(d, mask, keyname)
        try:
            # 这一次连接只用来探测，把所有抓取一次性放掉
            d.screen().root.ungrab_key(X.AnyKey, X.AnyModifier)
            d.sync()
        except Exception:
            pass
        if not grabbed and not bad:
            # 键名没有对应 keycode：不是「被占用」，别吓唬用户
            return None
        return bad
    finally:
        try:
            d.close()
        except Exception:
            pass


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


def bindings():
    """读出配置里的热键绑定：[(action, combo_text, mask, keyname), ...]。"""
    out = []
    if core.conf_bool('hotkey', 'enabled', True):
        text = core.conf('hotkey', 'combo', '<Control><Alt>c')
        parsed = parse_combo(text)
        if parsed:
            out.append(('gui', text, parsed[0], parsed[1]))
        else:
            core.log('唤起窗口的热键无法解析: %r' % text)
    if core.conf_bool('hotkey', 'copy_enabled', True):
        text = core.conf('hotkey', 'copy_combo', '<Control><Shift>c')
        parsed = parse_combo(text)
        if parsed:
            out.append(('copy', text, parsed[0], parsed[1]))
        else:
            core.log('复制最近文档的热键无法解析: %r' % text)
    return out


def copy_latest():
    """不开窗口，直接把最近一个文档放进剪贴板，并用桌面通知回话。

    通知是这条路径唯一的反馈渠道 —— 不弹窗口、没有状态栏，用户按完
    组合键只能靠通知知道「复制了哪个文件」或「为什么没成」。
    """
    from . import recent, service
    try:
        rows = recent.parse_recent(limit=1)
    except Exception as ex:
        core.log('读取最近文档失败: %r' % (ex,))
        service.notify('读取最近文档失败', str(ex))
        return False
    if not rows:
        core.log('没有最近文档可复制')
        service.notify('没有可复制的最近文档', '先用 WPS 打开一个文档，或打开窗口手动选')
        return False

    ok, msg = service.request_copy(rows[:1])
    if ok:
        brief = service.describe(rows[:1])
        core.log('热键复制 %s -> 成功' % rows[0])
        if core.conf_bool('ui', 'notify_on_copy', True):
            service.notify('已复制 %s' % brief, '切到桌面或微信按 Ctrl+V 即可粘贴')
    else:
        # 失败一定要说：不然用户只会觉得「按了没反应」
        core.log('热键复制 %s -> %s' % (rows[0], msg))
        service.notify('复制失败', msg)
    return ok


def run_action(action):
    if action == 'copy':
        copy_latest()
    else:
        launch_gui()


def main(argv=None):
    binds = bindings()
    if not binds:
        core.log('配置里没有启用任何热键，退出')
        return 0

    si = core.SingleInstance('hotkey')
    if not si.acquire():
        core.log('另一个热键进程已在运行，本次退出')
        return 0

    d = display.Display(core.conf('bridge', 'display', '') or None)
    root = d.screen().root

    handlers = {}        # (keycode, 修饰状态) -> action
    grabbed_all = []
    conflicts = []
    for action, text, mask, keyname in binds:
        grabbed, bad = grab_all(d, mask, keyname)
        if not grabbed:
            core.log('热键 %s 抓取失败，可能已被其它程序占用' % text)
            conflicts.append((text, 4))
            continue
        if bad:
            # 部分修饰状态被别的程序占了（比如搜狗/输入法、截图工具）
            core.log('热键 %s 有 %d 组修饰状态被其它程序占用' % (text, bad))
            conflicts.append((text, bad))
        keysym = XK.string_to_keysym(keyname)
        keycode = d.keysym_to_keycode(keysym)
        for kc, m in grabbed:
            handlers[(kc, m)] = action
        grabbed_all.extend(grabbed)
        core.log('热键 %s -> %s 已注册（%d 组修饰状态）'
                 % (text, action, len(grabbed)))

    if not grabbed_all:
        core.log('一个热键都没抓到，退出')
        si.release()
        return 1

    if conflicts:
        # 静默失效最难查：明确告诉用户换个组合键
        from . import service
        detail = '；'.join('%s（%d/4 组）' % (t, n) for t, n in conflicts)
        service.notify('快捷键没抢到', '%s 被其他程序占用了，在设置里换一个组合键'
                       % detail)

    last = {}
    mask_bits = X.ControlMask | X.Mod1Mask | X.Mod4Mask | X.ShiftMask
    try:
        while True:
            try:
                e = d.next_event()
            except Exception as ex:
                core.log('event err %r' % (ex,))
                time.sleep(0.5)
                continue
            if e.type == X.KeyPress:
                action = handlers.get((e.detail, e.state & mask_bits))
                if action is None:
                    continue
                now = time.time()
                if now - last.get(action, 0.0) < DEBOUNCE:
                    continue
                last[action] = now
                run_action(action)
            elif e.type == X.MappingNotify:
                # 键盘布局变化后重新抓取
                try:
                    root.ungrab_key(X.AnyKey, X.AnyModifier)
                except Exception:
                    pass
                for kc, m in grabbed_all:
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
