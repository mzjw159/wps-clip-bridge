#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""命令行入口 ``wps-copy``。

用法::

    wps-copy                 # 复制最近修改的第 1 个文档
    wps-copy --list          # 列出最近文档（置顶的标 ★）
    wps-copy --list 报告      # 只列名字/目录含「报告」的（多个词空格分开，要都命中）
    wps-copy --list --sort size -r   # 按大小从大到小列
    wps-copy --list --json   # 给脚本用：JSON 数组，带大小、时间、来源
    wps-copy 3               # 复制清单里第 3 个
    wps-copy 季度 报告        # 复制名字里同时含「季度」「报告」的（多个命中则列候选）
    wps-copy -f a.pdf b.doc  # 直接指定文件（可多个）
    wps-copy --pin a.docx    # 置顶（可多个；不带参数则列出当前置顶）
    wps-copy --unpin a.docx  # 取消置顶
    wps-copy --gui           # 打开图形界面
    wps-copy --start         # 立即启动后台服务（装完不用重启登录）
    wps-copy --stop          # 停止后台服务
    wps-copy --status        # 查看后台服务状态
    wps-copy --doctor        # 自检：数据源、依赖、会话类型、服务状态
    wps-copy --migrate       # 清理旧的手工部署（首次安装后跑一次）
"""
import json
import os
import subprocess
import sys
import unicodedata

from . import core, recent, service, wlclip


def usage(code=0):
    sys.stdout.write(__doc__.strip() + '\n')
    return code


def _disp_width(s):
    """终端里占几列：CJK 全角算 2 列。

    不这么算，中文名一多列就对不齐 —— 路径列一歪，「扫一眼找文件」就变慢。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)


def _pad(s, width):
    return s + ' ' * max(0, width - _disp_width(s))


def _stat(p, opened=None):
    """(大小文字, 最近打开文字, 用于排序的时间戳, 字节数, 文件修改时间)。

    时间列显示「最近打开」而不是文件修改时间：清单顺序按它排，列却显示另一
    个时间的话，第 1 行写着「03-15」而实际是今天刚打开的，自相矛盾。
    ``opened`` 是 ``{路径: 打开时间}``，没有记录时才退回 st_mtime。
    最后一项始终是 st_mtime —— ``--json`` 里两个时间都要给，脚本按老字段取
    ``mtime`` 的习惯不能破。

    拿不到就退化，别让整行输出挂掉。``--json`` 里两个大小都给：人看的和
    脚本算的（"4.0 KB" 没法直接比较大小）。
    """
    try:
        st = os.stat(p)
    except OSError:
        return '?', '?', 0, 0, 0
    import datetime
    ts = (opened or {}).get(p) or st.st_mtime
    return (recent._fmt_size(st.st_size),
            datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M'),
            ts, st.st_size, st.st_mtime)


def _entries(only_pinned=False):
    """清单条目：[(路径, 是否置顶)]。默认顺序与 ``wps-copy <序号>`` 一致。

    序号按「最近打开」排（不是置顶在前）—— 这样老用户照着清单敲的序号含义
    没变，置顶只在前面加个 ★ 做标记。
    """
    pins = core.pinned_paths()
    if only_pinned:
        return [(p, True) for p in pins if os.path.isfile(p)]
    pinset = set(pins)
    rows = recent.parse_recent(limit=core.conf_int('ui', 'limit', 80))
    return [(p, p in pinset) for p in rows]


def cmd_list(args):
    as_json = False
    only_pinned = False
    sort_field = ''
    reverse = False
    words = []
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a in ('--json', '-j'):
            as_json = True
        elif a in ('--pinned', '-p', '--pin-only'):
            only_pinned = True
        elif a in ('--sort', '-s'):
            if i >= len(args):
                print('--sort 后面要跟字段：%s' % '、'.join(recent.SORT_LABELS))
                return 2
            sort_field = args[i]
            i += 1
            if sort_field not in recent.SORT_LABELS:
                print('不认识的排序字段：%s（可选：%s）'
                      % (sort_field, '、'.join(recent.SORT_LABELS)))
                return 2
        elif a in ('--reverse', '-r'):
            reverse = True
        elif a in ('-h', '--help'):
            return usage(0)
        elif a.startswith('-'):
            print('未知选项：%s' % a)
            return 2
        else:
            # 一个参数里可能带空格（"季度 报告"），拆成多个词
            words.extend(recent.split_terms(a))

    rows = _entries(only_pinned=only_pinned)
    # 时间列与 --sort mtime 都用这份「最近打开」表（缺记录的退回 st_mtime）
    opened = recent.open_times()
    if words:
        rows = [(p, pin) for (p, pin) in rows if recent.match_terms(p, words)]

    if sort_field:
        # 排序要用原始字节数和时间戳；顺手把 stat 结果留着，下面打印不再重算
        stats = {p: _stat(p, opened) for p, _pin in rows}
        ordered = recent.sort_entries(
            [(p, stats[p][3], stats[p][2]) for p, _pin in rows],
            sort_field, reverse)
        pin_of = dict(rows)
        rows = [(p, pin_of[p]) for p, _nbytes, _mt in ordered]

    if as_json:
        out = []
        for p, pin in rows:
            size, when, ts, nbytes, mtime = _stat(p, opened)
            out.append({
                'path': p,
                'name': os.path.basename(p),
                'dir': os.path.dirname(p),
                'kind': recent.type_label(p),
                'source': recent.source_of(p),
                'size': size,
                'size_bytes': nbytes,
                'mtime': mtime,
                'opened': opened.get(p) or None,
                'when': when,
                'pinned': pin,
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        if words or only_pinned:
            scope = '置顶的文档' if only_pinned else '文档'
            kw = '、'.join(words)
            print('没有符合条件的%s%s。' % (scope, '（关键词：%s）' % kw if kw else ''))
            if only_pinned:
                print('用 wps-copy --pin <文件> 置顶，或 wps-copy --list 看全部。')
            else:
                print('去掉关键词再试，或 wps-copy --doctor 看数据源。')
            return 1
        print('没有读到 WPS 最近文档。')
        print(recent.diagnose()['hint'])
        print('详细自检：wps-copy --doctor')
        return 1

    width = len(str(len(rows)))
    kind_w = max(_disp_width(recent.type_label(p)) for p, _ in rows)
    size_w = max(_disp_width(_stat(p)[0]) for p, _ in rows)
    for i, (p, pin) in enumerate(rows, 1):
        size, when, _ts, _nb, _mt = _stat(p, opened)
        # 大小右对齐（数字比宽度有意义），类型左对齐（中文还好，PDF 是三个字符）
        print('%*d. %s %s  %s  %s  %s'
              % (width, i, '★' if pin else ' ',
                 _pad(recent.type_label(p), kind_w),
                 ' ' * (size_w - _disp_width(size)) + size,
                 when, p))
    npin = sum(1 for _p, pin in rows if pin)
    print('\n共 %d 个%s。复制：wps-copy <序号> 或 wps-copy <关键词>'
          % (len(rows), '（%d 个置顶）' % npin if npin else ''))
    print('  给脚本用：wps-copy --list --json')
    return 0


def cmd_pin(args, add=True):
    """置顶 / 取消置顶。不带参数时列出当前置顶，顺便当用法提示。"""
    pins = core.pinned_paths()
    if not args:
        if not pins:
            print('还没有置顶的文档。用法：wps-copy --pin <文件> [<文件>…]')
            return 0
        print('当前置顶 %d 个：' % len(pins))
        for i, p in enumerate(pins, 1):
            mark = '' if os.path.isfile(p) else '  （文件不在了）'
            print('  %d. %s%s' % (i, p, mark))
        print('\n取消：wps-copy --unpin <文件>')
        return 0

    changed, bad, todo = [], [], []
    for a in args:
        p = os.path.abspath(os.path.expanduser(a))
        if not os.path.isfile(p):
            bad.append(p)
            continue
        if p not in todo:
            todo.append(p)

    if add:
        # 新钉的排在最前（刚钉的就是当下最关心的），但一条命令里钉多个时
        # 保留命令行里的先后顺序，别被倒过来。
        changed = [p for p in todo if p not in pins]
        pins = changed + pins
    else:
        changed = [p for p in todo if p in pins]
        pins = [p for p in pins if p not in changed]
    if changed:
        core.set_pinned(pins)
    for p in changed:
        print('✔ %s：%s' % ('已置顶' if add else '已取消置顶', p))
    for p in bad:
        print('✘ 文件不存在：%s' % p)
    if not changed and not bad:
        print('已经%s了，无需改动。' % ('置顶' if add else '取消置顶'))
    print('当前置顶 %d 个（wps-copy --pin 可查看列表）。' % len(pins))
    return 0 if not bad else 1


def pick(args):
    """把参数解析成文件路径列表。"""
    # 与 --list 用同一份清单：清单第 3 行必须就是 wps-copy 3 复制的东西
    rows = [p for p, _pin in _entries()]
    # 多个词拼起来一起匹配：「wps-copy 季度 报告」和图形界面里搜「季度 报告」
    # 是同一个语义（每个词都要命中）
    target = ' '.join(args).strip()
    terms = recent.split_terms(target)

    if target.isdigit():
        idx = int(target)
        if not (1 <= idx <= len(rows)):
            print('序号超出范围：清单共 %d 个' % len(rows))
            return None
        return [rows[idx - 1]]

    hits = [p for p in rows
            if recent.match_terms(os.path.basename(p), terms)]
    if not hits:
        hits = [p for p in rows if recent.match_terms(p, terms)]
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
                     'XWayland 应用有焦点时才收得到；热键不灵时用托盘菜单或命令行。'
                     '复制到纯 Wayland 应用要靠 wl-copy（见下面的依赖检查）。')
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
                      ('wl-copy', 'wl-clipboard（Wayland 会话里复制文件到纯 Wayland '
                                  '应用需要它，X11 下用不到）'),
                      ('import', 'imagemagick（只在截图调试时用，可不装）')):
        found = any(os.access(os.path.join(p, exe), os.X_OK)
                    for p in os.environ.get('PATH', '').split(':'))
        print('  %s %-12s %s' % ('✔' if found else '·', exe,
                                 '已安装' if found else '未安装（' + hint + '）'))
    if sess == 'wayland' and not wlclip.available():
        mark('bad', 'Wayland 会话但没有 wl-copy：复制到纯 Wayland 应用（GTK4 原生、'
                    'Qt6 wayland）可能粘不出文件。安装：sudo apt install wl-clipboard')

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
    print('    打开时间 %d 条（决定列表顺序；0 条时退回按文件修改时间）'
          % info['opened'])
    if info['existing'] and not info['opened']:
        mark('warn', '一条打开时间都没读到 —— 列表会按文件修改时间排，'
                     '「打开老文档但不修改」不会挪到最前面。'
                     'WPS 可能改了 RecentFiles\\Sequence 的写法，见 docs/DATA-SOURCE.md。')
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
    if args[0] in ('--pin', 'pin', '--pinned'):
        return cmd_pin(args[1:], add=True)
    if args[0] in ('--unpin', 'unpin'):
        return cmd_pin(args[1:], add=False)
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
