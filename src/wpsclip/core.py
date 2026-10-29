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
VERSION = '1.4.1'

# 默认配置；用户可在 ~/.config/wps-clip-bridge/config.ini 覆盖
DEFAULTS = {
    'hotkey': {
        'enabled': 'true',
        'combo': '<Control><Alt>c',
        # 第二个热键：不用开窗口，直接把最近一个文档丢进剪贴板
        'copy_enabled': 'true',
        'copy_combo': '<Control><Alt>v',
    },
    'bridge': {
        # 剪贴板轮询间隔（秒），越小越灵敏、越耗电
        'poll': '0.3',
    },
    'ui': {
        # 列表最多显示多少条
        'limit': '80',
        # 主题：system（跟随桌面）/ light / dark
        'theme': 'system',
        # 关闭窗口时收进托盘而不是退出
        'close_to_tray': 'true',
        # 复制成功后自动把窗口收起来
        'hide_after_copy': 'true',
        # 启动时直接待在托盘里，不弹窗
        'start_hidden': 'false',
        # 复制成功后发系统通知
        'notify_on_copy': 'true',
        # 托盘后端：auto / indicator / statusicon
        'tray_backend': 'auto',
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
_config_mtime = None


def config():
    """读配置；文件被改过（mtime 变了）就自动重读。

    设置窗口写完文件后，守护进程、热键进程都能立刻看到新值，
    不用重启它们。
    """
    global _config, _config_mtime
    path = os.path.join(config_home(), 'config.ini')
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        mt = None
    if _config is not None and mt == _config_mtime:
        return _config
    cp = configparser.ConfigParser()
    for section, items in DEFAULTS.items():
        cp[section] = dict(items)
    try:
        cp.read(path, encoding='utf-8')
    except Exception:
        pass
    _config = cp
    _config_mtime = mt
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


def config_file():
    return os.path.join(config_home(), 'config.ini')


def save_conf(updates):
    """把一批配置写回 config.ini。

    ``updates`` 形如 ``{('ui', 'theme'): 'dark', ...}``。写临时文件再
    os.replace，避免写到一半断电留下坏文件。写完清缓存，下次 conf() 读新值。
    """
    global _config, _config_mtime
    cp = config()
    for (section, key), value in updates.items():
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, '' if value is None else str(value))
    path = config_file()
    _ensure_dir(config_home())
    tmp = path + '.tmp%d' % os.getpid()
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            cp.write(f)
        os.replace(tmp, path)
    except OSError as ex:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ex
    _config = None
    _config_mtime = None
    return path


# ---------------------------------------------------------------- 开机自启

AUTOSTART_NAME = 'wps-clip-bridge.desktop'
SYSTEM_AUTOSTART = '/etc/xdg/autostart/' + AUTOSTART_NAME


def autostart_file():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'autostart', AUTOSTART_NAME)


def autostart_enabled():
    """用户目录里没有覆盖文件 → 用系统的自启项（即已启用）。

    用户目录里写了 ``Hidden=true`` 就是对系统自启项的屏蔽。
    """
    p = autostart_file()
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                text = f.read()
        except OSError:
            text = ''
        for line in text.splitlines():
            if line.strip().lower().replace(' ', '') == 'hidden=true':
                return False
        return True
    if os.path.exists(SYSTEM_AUTOSTART):
        return True
    return False


def set_autostart(on):
    """勾选开机自启 / 取消勾选，返回最终状态。"""
    p = autostart_file()
    _ensure_dir(os.path.dirname(p))
    if on:
        # 系统里已经有自启项，删掉用户目录的屏蔽文件即可
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass
        if not os.path.exists(SYSTEM_AUTOSTART):
            # 没装包（源码里跑）时自己写一个
            with open(p, 'w', encoding='utf-8') as f:
                f.write('[Desktop Entry]\n'
                        'Type=Application\n'
                        'Name=WPS 剪贴板服务\n'
                        'Exec=/usr/bin/wps-clip-bridge-daemon\n'
                        'Icon=wps-file\n'
                        'Terminal=false\n'
                        'NoDisplay=true\n'
                        'X-GNOME-Autostart-enabled=true\n')
    else:
        with open(p, 'w', encoding='utf-8') as f:
            f.write('[Desktop Entry]\n'
                    'Type=Application\n'
                    'Name=WPS 剪贴板服务（已禁用）\n'
                    'Exec=/usr/bin/wps-clip-bridge-daemon\n'
                    'Icon=wps-file\n'
                    'Terminal=false\n'
                    'NoDisplay=true\n'
                    'Hidden=true\n'
                    'X-GNOME-Autostart-enabled=false\n')
    return autostart_enabled()


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


# 请求文件超过这个岁数就算「上一轮遗留」，启动时不执行
STALE_REQUEST_SECONDS = 60.0


def request_is_stale(mt, now=None, stale=STALE_REQUEST_SECONDS):
    """判断请求文件是不是上一轮遗留的。

    bridge 启动时用：旧请求别再执行一遍（否则一登录就把上次复制的文件
    又塞回剪贴板）；但**刚刚**写下的请求必须照办 —— 冷启动时界面可能比
    bridge 初始化更早写下请求，把它当历史忽略，表现就是「第一次复制没反应」。
    """
    if mt is None:
        return False
    if now is None:
        now = time.time()
    return (now - mt) > stale


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

