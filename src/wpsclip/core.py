#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""公共基础设施：版本、XDG 路径、配置、日志、单实例锁。

所有运行期文件都放在 XDG 目录里，不写死家目录路径，
这样同一个包可以给多个用户、多台机器用。
"""
import configparser
import fcntl
import os
import sys
import time

APP = 'wps-clip-bridge'
VERSION = '1.0.0'

# 默认配置；用户可在 ~/.config/wps-clip-bridge/config.ini 覆盖
DEFAULTS = {
    'hotkey': {
        'enabled': 'true',
        'combo': '<Control><Alt>c',
    },
    'bridge': {
        # 剪贴板轮询间隔（秒），越小越灵敏、越耗电
        'poll': '0.3',
    },
    'ui': {
        # 列表最多显示多少条
        'limit': '80',
    },
}


# ---------------------------------------------------------------- 路径

def _ensure_dir(path, mode=0o755):
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
    except OSError:
        pass
    return path


def runtime_dir():
    """运行时目录：锁文件、请求文件、PID。重启后自动清理。"""
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base or not os.path.isdir(base):
        # 兜底：用 /tmp 下带 uid 的私有目录
        base = os.path.join('/tmp', 'wps-clip-bridge-%d' % os.getuid())
        _ensure_dir(base, 0o700)
    return _ensure_dir(os.path.join(base, APP), 0o700)


def state_dir():
    """持久状态：日志。"""
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return _ensure_dir(os.path.join(base, APP))


def config_home():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, APP)


def runtime_file(name):
    return os.path.join(runtime_dir(), name)


def lock_path(name):
    return runtime_file(name + '.lock')


def req_path():
    return runtime_file('copy-request')


def gui_wake_path():
    """按下热键时摸一下这个文件，已在运行的窗口会自己跳到前台。"""
    return runtime_file('gui-wake')


# ---------------------------------------------------------------- 配置

_config = None


def config():
    global _config
    if _config is not None:
        return _config
    cp = configparser.ConfigParser()
    for section, items in DEFAULTS.items():
        cp[section] = dict(items)
    path = os.path.join(config_home(), 'config.ini')
    try:
        cp.read(path, encoding='utf-8')
    except Exception:
        pass
    _config = cp
    return cp


def conf(section, key, fallback=''):
    try:
        return config().get(section, key, fallback=fallback)
    except Exception:
        return DEFAULTS.get(section, {}).get(key, fallback)


def conf_bool(section, key, fallback=True):
    v = conf(section, key, 'true' if fallback else 'false').strip().lower()
    return v in ('1', 'true', 'yes', 'on')


def conf_float(section, key, fallback):
    try:
        return float(conf(section, key, str(fallback)))
    except ValueError:
        return fallback


def conf_int(section, key, fallback):
    try:
        return int(conf(section, key, str(fallback)))
    except ValueError:
        return fallback


# ---------------------------------------------------------------- 日志

_log_file = None


def set_log_file(path):
    """守护进程用：把日志重定向到文件，并加简单轮转。"""
    global _log_file
    _log_file = path
    try:
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.replace(path, path + '.1')
    except OSError:
        pass


def log(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    if _log_file:
        try:
            with open(_log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
            return
        except OSError:
            pass
    try:
        sys.stderr.write(line + '\n')
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 单实例

class SingleInstance(object):
    """用 flock 做物理互斥。

    比 pgrep -f 可靠：不会匹配到命令行里恰好含同样字样的其他进程。
    """

    def __init__(self, name):
        self.name = name
        self.path = lock_path(name)
        self.fd = None

    def acquire(self):
        """拿到锁返回 True；已有实例在跑返回 False。"""
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        self.fd = fd
        return True

    def release(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def is_running(name):
    """不抢锁地探测某个实例是否在跑。"""
    path = lock_path(name)
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True          # 别人持有 => 在跑
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def touch(path):
    try:
        with open(path, 'a'):
            pass
        os.utime(path, None)
    except OSError:
        pass


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


# ---------------------------------------------------------------- 剪贴板读取

def read_clipboard_uris(timeout=1.2):
    """读当前 X11 CLIPBOARD 的 text/uri-list，用于确认复制是否真的生效。"""
    try:
        from Xlib import X, display
    except ImportError:
        return None
    d = None
    try:
        d = display.Display(conf('bridge', 'display', '') or None)
        scr = d.screen()
        w = scr.root.create_window(0, 0, 1, 1, 0, scr.root_depth)
        CLIP = d.intern_atom('CLIPBOARD')
        TMP = d.intern_atom('_WPS_CLIP_READ_TMP')
        URI = d.intern_atom('text/uri-list')
        w.convert_selection(CLIP, URI, TMP, X.CurrentTime)
        d.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if d.pending_events():
                e = d.next_event()
                if e.type == X.SelectionNotify and e.target == URI:
                    if e.property == X.NONE:
                        return None
                    p = w.get_full_property(TMP, X.AnyPropertyType)
                    if p is None:
                        return None
                    v = p.value
                    return v.decode('utf-8', 'replace') if isinstance(v, bytes) else None
            else:
                time.sleep(0.01)
    except Exception:
        return None
    finally:
        try:
            if d is not None:
                d.close()
        except Exception:
            pass
    return None


def clipboard_has(path):
    """剪贴板里是否包含指定文件（路径或 file:// URI 任一形式）。"""
    uris = read_clipboard_uris()
    if not uris:
        return False
    import urllib.parse
    return (('file://' + urllib.parse.quote(path, safe='/')) in uris) or (path in uris)

