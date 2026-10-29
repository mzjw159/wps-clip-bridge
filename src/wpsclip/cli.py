#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""命令行入口 ``wps-copy``。

用法::

    wps-copy                 # 复制最近修改的第 1 个文档
    wps-copy --list          # 列出最近文档
    wps-copy 3               # 复制清单里第 3 个
    wps-copy 报告            # 复制文件名含「报告」的（多个命中则列候选）
    wps-copy -f a.pdf b.doc  # 直接指定文件（可多个）
    wps-copy --gui           # 打开图形界面
    wps-copy --start         # 立即启动后台服务（装完不用重启登录）
    wps-copy --stop          # 停止后台服务
    wps-copy --status        # 查看后台服务状态
    wps-copy --doctor        # 自检：数据源、依赖、会话类型、服务状态
    wps-copy --migrate       # 清理旧的手工部署（首次安装后跑一次）
"""
import os
import subprocess
import sys

from . import core, recent, service


def usage(code=0):
    sys.stdout.write(__doc__.strip() + '\n')
    return code


def cmd_list(args):
    rows = recent.parse_recent(limit=core.conf_int('ui', 'limit', 80))
    if not rows:
        print('没有读到 WPS 最近文档。')
        print(recent.diagnose()['hint'])
        print('详细自检：wps-copy --doctor')
        return 1
    width = len(str(len(rows)))
    for i, p in enumerate(rows, 1):
        print('%*d. [%s] %s' % (width, i, recent.kind_of(p), p))
    print('\n共 %d 个。用 wps-copy <序号> 或 <关键词> 复制。' % len(rows))
    return 0


def pick(args):
    """把参数解析成文件路径列表。"""
    rows = recent.parse_recent(limit=core.conf_int('ui', 'limit', 80))
    target = args[0]

    if target.isdigit():
        idx = int(target)
        if not (1 <= idx <= len(rows)):
            print('序号超出范围：清单共 %d 个' % len(rows))
            return None
        return [rows[idx - 1]]

    hits = [p for p in rows if target.lower() in os.path.basename(p).lower()]
    if not hits:
        hits = [p for p in rows if target.lower() in p.lower()]
    if not hits:
        print('没有匹配「%s」的文档。用 wps-copy --list 看看清单。' % target)
        return None
    if len(hits) == 1:
        return hits
    print('「%s」匹配到 %d 个，请改用序号：' % (target, len(hits)))
    for i, p in enumerate(rows, 1):
        if p in hits:
            print('  %d. %s' % (i, p))
    return None


def cmd_copy(args):
    if not args:
        rows = recent.parse_recent(limit=1)
        if not rows:
            print('没有读到 WPS 最近文档。%s' % recent.diagnose()['hint'])
            print('也可以：wps-copy -f <文件> 直接指定，或 wps-copy --gui 里「浏览本地文件」。')
            return 1
        paths = rows
    else:
        paths = pick(args)
        if paths is None:
            return 1

    ok, msg = service.request_copy(paths)
    if not ok:
        print('✘ %s' % msg)
        return 1
    print('✔ 已复制：%s' % service.describe(paths))
    for p in paths[:5]:
        print('    %s' % p)
    if len(paths) > 5:
        print('    …（共 %d 个）' % len(paths))
    print('\n现在切到桌面或微信窗口，按 Ctrl+V 粘贴。')
    print('提示：不要再去 WPS 里点「复制」，那个按钮会把剪贴板清空。')
    return 0


def cmd_files(args):
    if not args:
        print('请跟上文件路径：wps-copy -f <文件> [<文件>…]')
        return 1
    ok, msg = service.request_copy(args)
    if not ok:
        print('✘ %s' % msg)
        return 1
    print('✔ 已复制：%s' % service.describe(args))
    return 0


def cmd_start(_args):
    if service.ensure_bridge():
        print('✔ 后台服务已启动（剪贴板服务%s）'
              % ('、全局热键' if core.conf_bool('hotkey', 'enabled', True) else ''))
        print('  现在可以直接用 wps-copy 或 Ctrl+Alt+C 了。')
        return 0
    print('✘ 启动失败，请看日志：%s'
          % os.path.join(core.state_dir(), 'daemon.log'))
    return 1


def cmd_stop(_args):
    if not core.is_running('daemon'):
        print('后台服务本来就没在运行。')
        return 0
    if service.stop_daemon():
        print('✔ 后台服务已停止。')
        return 0
    print('✘ 停止失败，可以手动结束：pkill -f wps-clip-bridge-daemon')
    return 1


def cmd_status(_args):
    print('本机服务状态：')
    for name, label in (('daemon', '守护进程'), ('bridge', '剪贴板服务'),
                        ('hotkey', '全局热键'), ('gui', '图形窗口')):
        mark = '运行中' if core.is_running(name) else '未运行'
        print('  %-6s %-10s %s' % (label, name, mark))
    print('\n快捷键：')
    from .settings import pretty_combo
    for enabled, combo, label in (
            (core.conf_bool('hotkey', 'enabled', True),
             core.conf('hotkey', 'combo', '<Control><Alt>c'), '唤起窗口'),
            (core.conf_bool('hotkey', 'copy_enabled', True),
             core.conf('hotkey', 'copy_combo', '<Control><Alt>v'), '复制最近文档')):
        print('  %-8s %s%s' % (label, pretty_combo(combo) if enabled else '已禁用',
                               '' if enabled else ''))
    print('\n主题：%s   关闭时收进托盘：%s   开机自启：%s'
          % (core.conf('ui', 'theme', 'system'),
             '是' if core.conf_bool('ui', 'close_to_tray', True) else '否',
             '是' if core.autostart_enabled() else '否'))
    print('\n运行期目录：%s' % core.runtime_dir())
    print('日志目录：  %s' % core.state_dir())
    print('配置目录：  %s' % core.config_home())
    print('最近文档：  %d 个' % len(recent.parse_recent(limit=999)))
    if not core.is_running('bridge'):
        print('\n后台服务未运行，执行 wps-copy --start 立即启动。')
    return 0


def cmd_doctor(_args):
    """一条命令看全部关键点：数据源、剪贴板服务、依赖、会话类型。

    分享给别人的机器上出问题时，让对方把这一段贴出来，比来回问
    「你装 WPS 了吗」「你开的是 X11 还是 Wayland」快得多。
    """
    import platform

    from . import recent as rn

    ok, warn, bad = [], [], []

    def mark(level, text):
        (ok if level == 'ok' else warn if level == 'warn' else bad).append(text)

    print('%s %s 自检' % (core.APP, core.VERSION))
    print('系统：%s %s / Python %s'
          % (platform.system(), platform.machine(), platform.python_version()))
    sess = os.environ.get('XDG_SESSION_TYPE', '') or '未知'
    disp = os.environ.get('DISPLAY', '') or '（空）'
    print('会话：XDG_SESSION_TYPE=%s  DISPLAY=%s' % (sess, disp))
    if sess == 'wayland':
        mark('warn', '当前是 Wayland 会话：程序通过 XWayland 工作，X11 全局热键只在'
                     'XWayland 应用有焦点时才收得到；热键不灵时用托盘菜单或命令行。')
    if not os.environ.get('DISPLAY'):
        mark('bad', '没有 DISPLAY —— 剪贴板服务需要 X11（XWayland 也算）。')

    print('\n依赖：')
    for mod, hint in (('gi', 'python3-gi / gir1.2-gtk-3.0'),
                      ('Xlib', 'python3-xlib')):
        try:
            m = __import__(mod)
            extra = ''
            if mod == 'Xlib':
                # 这份 Xlib 是系统装的还是包里自带的？出问题时这一步很关键：
                # 自带的那份版本固定，「升级了 python3-xlib 之后坏了」就不成立。
                ver = getattr(m, '__version__', None)
                if isinstance(ver, tuple):
                    ver = '.'.join(str(x) for x in ver)
                where = ('内置' if '_vendor' in (getattr(m, '__file__', '') or '')
                         else '系统')
                extra = '  %s（%s）' % (ver or '?', where)
            print('  ✔ %-8s%s' % (mod, extra))
        except Exception:
            print('  ✘ %-8s 缺少 %s' % (mod, hint))
            mark('bad', '缺少 Python 模块 %s（%s）' % (mod, hint))
    for exe, hint in (('notify-send', 'libnotify-bin（通知，可选）'),
                      ('import', 'imagemagick（只在截图调试时用，可不装）')):
        found = any(os.access(os.path.join(p, exe), os.X_OK)
                    for p in os.environ.get('PATH', '').split(':'))
        print('  %s %-12s %s' % ('✔' if found else '·', exe,
                                 '已安装' if found else '未安装（' + hint + '）'))

    print('\n数据源：')
    info = rn.diagnose()
    files = info['conf_files']
    if files:
        for p, size in files:
            print('  ✔ %s（%d 字节）' % (p, size))
    else:
        print('  ✘ 没找到 %s' % rn.CONF)
        mark('bad', '没找到 WPS 配置文件，先确认装的是 WPS for Linux 并打开过一个文档。')
    print('    结构化解析 %d 条 / 通用扫描 %d 条 / 合计 %d 条 / 文件存在 %d 条'
          % (info['structured'], info['scanned'], info['total'], info['existing']))
    if info['total'] and not info['structured']:
        mark('warn', '结构化解析一条都没命中，但通用扫描捞到了 %d 条 —— '
                     'WPS 大概改了键名（这正是备用解析存在的意义）。'
                     % info['scanned'])
    if info['total'] and not info['existing']:
        mark('warn', '记录都在，但文件一个都不存在（移动/删除/盘没挂）。')

    print('  兜底数据源：')
    for c in rn.XBEL_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.exists(p):
            n = len(rn.parse_xbel(open(p, 'rb').read().decode('utf-8', 'replace')))
            print('    ✔ %s（%d 条本地文档）' % (p, n))
            break
    else:
        print('    · 没有 GTK 最近使用记录')
    for c in rn.CLOUD_DIRS:
        p = os.path.expanduser(c)
        if os.path.isdir(p):
            print('    ✔ 云文档目录 %s' % p)
            break
    else:
        print('    · 没有 WPS 云文档目录')

    print('\n后台服务：')
    for name, label in (('daemon', '守护进程'), ('bridge', '剪贴板服务'),
                        ('hotkey', '全局热键'), ('gui', '图形窗口')):
        running = core.is_running(name)
        print('  %s %-6s %s' % ('✔' if running else '·', label,
                                '运行中' if running else '未运行'))
    if not core.is_running('bridge'):
        mark('warn', '剪贴板服务没在跑；第一次复制会自动拉起（也可以先跑 wps-copy --start）。')

    print('\n诊断结论：')
    if info['hint']:
        print('  %s' % info['hint'])
    for msg in bad:
        print('  ✘ %s' % msg)
    for msg in warn:
        print('  ! %s' % msg)
    if not bad and not warn and not info['hint']:
        print('  一切正常。')
    print('\n日志：%s' % os.path.join(core.state_dir(), 'daemon.log'))
    print('目录：运行期 %s' % core.runtime_dir())
    return 0 if not bad else 2


def cmd_gui(_args):
    try:
        subprocess.Popen(service.exe_for('wps-copy-gui', 'wpsclip.gui'),
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return 0
    except Exception as ex:
        print('启动图形界面失败：%s' % ex)
        return 1


def cmd_migrate(_args):
    """清理旧的手工部署，避免和安装包抢剪贴板 / 抢热键。"""
    import glob

    home = os.path.expanduser('~')
    removed, killed = [], []

    # 1) 旧的登录自启项
    for pat in ('wps-hotkey.desktop', 'wps-clip-bridge.desktop'):
        for p in glob.glob(os.path.join(home, '.config/autostart', pat)):
            try:
                os.remove(p)
                removed.append(p)
            except OSError:
                pass

    # 2) 旧的手工进程（只认旧路径，不动 /usr 下的新程序）
    old_marks = ('wps_clip_bridge.py', 'wps-recent-copy.py',
                 'run-forever.sh', 'hotkey.py')
    me = os.getpid()
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open('/proc/%s/cmdline' % pid, 'rb') as f:
                cmd = f.read().decode('utf-8', 'replace')
        except OSError:
            continue
        if not any(m in cmd for m in old_marks):
            continue
        if '/usr/lib/python3/dist-packages/wpsclip' in cmd or '/usr/bin/wps-' in cmd:
            continue
        try:
            os.kill(int(pid), 15)
            killed.append('%s (%s)' % (pid, cmd.split('\x00')[1]
                                       if '\x00' in cmd else cmd[:40]))
        except OSError:
            pass

    # 3) 旧的运行期残留
    for p in ('~/.wps-clip-bridge.req', '~/.cache/wps-hotkey.log',
              '~/.cache/wps-hotkey-gui.out', '~/.cache/wps-gui.pid',
              '~/.cache/wps-clip-bridge.log'):
        full = os.path.expanduser(p)
        if os.path.exists(full):
            try:
                os.remove(full)
                removed.append(full)
            except OSError:
                pass

    if removed:
        print('已删除：')
        for p in removed:
            print('  %s' % p)
    if killed:
        print('已结束旧进程：')
        for k in killed:
            print('  %s' % k)
    if not removed and not killed:
        print('没有发现旧的手工部署，无需迁移。')

    print('\n旧部署目录 ~/WorkBuddy/files/services/wps-clip-bridge 可以自行删除。')
    print('现在运行 wps-copy --status 确认新的服务已在运行。')
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        return cmd_copy([])
    if args[0] in ('-h', '--help', 'help'):
        return usage(0)
    if args[0] in ('-V', '--version'):
        print('%s %s' % (core.APP, core.VERSION))
        return 0
    if args[0] in ('-l', '--list', 'list'):
        return cmd_list(args[1:])
    if args[0] in ('-g', '--gui', 'gui'):
        return cmd_gui(args[1:])
    if args[0] in ('-s', '--status', 'status'):
        return cmd_status(args[1:])
    if args[0] in ('--doctor', 'doctor'):
        return cmd_doctor(args[1:])
    if args[0] in ('--migrate', 'migrate'):
        return cmd_migrate(args[1:])
    if args[0] in ('--start', 'start'):
        return cmd_start(args[1:])
    if args[0] in ('--stop', 'stop'):
        return cmd_stop(args[1:])
    if args[0] in ('-f', '--file', 'file'):
        return cmd_files(args[1:])
    return cmd_copy(args)


if __name__ == '__main__':
    sys.exit(main())
