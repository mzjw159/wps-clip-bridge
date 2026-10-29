#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""各入口共用的服务编排：确保后台在跑、发起复制请求、确认结果。"""
import os
import subprocess
import sys
import time

from . import core

DAEMON_CMD = '/usr/bin/wps-clip-bridge-daemon'
READY_TIMEOUT = 3.0
# 冷启动（守护进程 -> 剪贴板桥）在慢机器上可能要好几秒。界面里复制已经
# 在后台线程里跑，多等一会儿不影响手感，比「第一次复制失败」体面。
COLD_START_TIMEOUT = 8.0


def exe_for(name, module):
    """优先用安装好的入口脚本；源码树里退回 python -m。"""
    path = '/usr/bin/' + name
    if os.path.exists(path):
        return [path]
    return [sys.executable, '-m', module]


def bridge_running():
    return core.is_running('bridge')


def ensure_bridge(wait=READY_TIMEOUT):
    """确保剪贴板服务在跑；没跑就拉起守护进程。"""
    if bridge_running():
        return True
    ensure_daemon()
    deadline = time.time() + wait
    while time.time() < deadline:
        if bridge_running():
            return True
        time.sleep(0.1)
    return False


def ensure_daemon():
    """启动守护进程（它自己会拉起 bridge + hotkey，并做崩溃重启）。"""
    if core.is_running('daemon'):
        return
    try:
        out = open(os.path.join(core.state_dir(), 'daemon-launch.log'), 'ab')
        subprocess.Popen(exe_for('wps-clip-bridge-daemon', 'wpsclip.daemon'),
                         stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True,
                         close_fds=True)
    except Exception as ex:
        core.log('拉起守护进程失败: %r' % (ex,))


def stop_daemon(timeout=5.0):
    """给守护进程发 SIGTERM，它会顺带停掉子进程。"""
    import signal

    pid = _lock_pid('daemon')
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not core.is_running('daemon'):
            return True
        time.sleep(0.1)
    return not core.is_running('daemon')


def _lock_pid(name):
    try:
        with open(core.lock_path(name)) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def prepare(paths):
    """规范化路径并剔除不存在的项，返回 (有效路径, 错误说明)。"""
    out = []
    for p in paths:
        if not p:
            continue
        ap = os.path.abspath(os.path.expanduser(p))
        if os.path.isfile(ap) and ap not in out:
            out.append(ap)
    if not out:
        return [], '没有可复制的文件（路径不存在）'
    return out, ''


def publish(paths):
    """把复制请求写成文件，返回写之前的 published 时间戳（作为标记）。

    bridge 进程轮询这个请求文件，发布成功后会摸一下 published 文件，
    调用方靠「published 的 mtime 变了」判断这一轮已经处理完。
    写临时文件再 os.replace，避免服务器读到半截内容。
    """
    marker = core.mtime(core.runtime_file('published'))
    req = core.req_path()
    tmp = '%s.tmp%d' % (req, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(paths) + '\n')
    os.replace(tmp, req)
    return marker


def wait_published(marker, timeout=3.0):
    """等 bridge 处理完这次请求（published 时间戳变化）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = core.mtime(core.runtime_file('published'))
        if cur is not None and cur != marker:
            return True
        time.sleep(0.05)
    return False


def request_copy(paths, timeout=3.0, on_stage=None):
    """请求后台把文件放进剪贴板，返回 (ok, message)。

    这是同步版本：会阻塞到后台确认或超时（最长约 3 秒）。图形界面不要在
    主线程里调用它，用 ``copy_async``；命令行/热键进程阻塞一下无所谓。

    ``on_stage`` 是可选的进度回调，参数为一句人话（"正在启动后台服务…"），
    给界面在等待期间刷新状态用。
    """
    def stage(text):
        if on_stage is not None:
            try:
                on_stage(text)
            except Exception:
                pass

    paths, err = prepare(paths)
    if err:
        return False, err

    if not bridge_running():
        stage('正在启动后台服务…')
    if not ensure_bridge(wait=COLD_START_TIMEOUT):
        return False, '后台服务未能启动，请查看 %s' % os.path.join(
            core.state_dir(), 'daemon.log')

    stage('正在写入剪贴板…')
    try:
        marker = publish(paths)
    except OSError as ex:
        return False, '写入请求失败: %s' % ex

    if not wait_published(marker, timeout):
        return False, '后台没有响应，请运行 wps-copy --status 检查'

    # 端到端确认：真的能从剪贴板读回这个文件（读不回也算成功：Wayland
    # 合成器偶尔会抢在中间，服务确实已经发布了）
    core.clipboard_has(paths[0])
    return True, ''


def copy_async(paths, on_done, on_stage=None):
    """后台线程里跑 request_copy，完成后调 ``on_done(ok, msg, paths)``。

    界面靠它消除「点一下卡三秒」——剪贴板桥冷启动、写入确认都在后台做，
    GTK 主循环始终能响应重绘和点击。线程是 daemon，程序退出不会被拖住。

    注意：``on_done`` / ``on_stage`` 都在**工作线程**里被调用，GUI 必须
    自己用 ``GLib.idle_add`` 切回主线程再碰控件。
    """
    import threading

    norm, err = prepare(paths)
    if err:
        on_done(False, err, [])
        return None

    def work():
        try:
            ok, msg = request_copy(norm, on_stage=on_stage)
        except Exception as ex:            # 线程里不能抛出去，转成失败结果
            core.log('复制任务异常: %r' % (ex,))
            ok, msg = False, '复制过程出错：%s' % ex
        on_done(ok, msg, norm)

    t = threading.Thread(target=work, name='wps-copy', daemon=True)
    t.start()
    return t


def warmup():
    """界面启动时先预热后台，省掉第一次复制的冷启动等待。"""
    try:
        ensure_bridge(wait=COLD_START_TIMEOUT)
    except Exception as ex:
        core.log('预热后台失败: %r' % (ex,))


def describe(paths):
    if not paths:
        return ''
    if len(paths) == 1:
        return os.path.basename(paths[0])
    return '%s 等 %d 个文件' % (os.path.basename(paths[0]), len(paths))


def restart_hotkey():
    """改完热键配置后重启热键进程，让新组合立即生效。

    先 SIGTERM 旧进程（它自己会释放 X 键盘抓取），再按新配置拉起一个。
    守护进程那边也会看护，靠单实例锁去重，不会出现两个。
    """
    import signal

    pid = _lock_pid('hotkey')
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline and core.is_running('hotkey'):
            time.sleep(0.05)
        if core.is_running('hotkey'):
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.2)
            except OSError:
                pass

    if not core.conf_bool('hotkey', 'enabled', True):
        return False
    if core.is_running('daemon'):
        # 守护进程每秒 tick 一次，会自动按新配置把它拉起来；
        # 这里再自己起一个只会让两边抢锁、互相把对方踢掉。
        return True
    try:
        out = open(os.path.join(core.state_dir(), 'hotkey.log'), 'ab')
        subprocess.Popen(exe_for('wps-copy-hotkey', 'wpsclip.hotkey'),
                         stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, start_new_session=True)
    except Exception as ex:
        core.log('重启热键进程失败: %r' % (ex,))
        return False
    return True


def _notify_icon():
    """通知里的图标：优先给绝对路径。

    `notify-send -i wps-file` 依赖图标主题查找，图标没装到主题里（比如直接
    从源码树跑）就什么都不显示。给路径最稳。
    """
    for p in ('/usr/share/icons/hicolor/256x256/apps/wps-file.png',
              '/usr/share/pixmaps/wps-file.png',
              '/usr/share/icons/hicolor/scalable/apps/wps-file.svg'):
        if os.path.exists(p):
            return p
    return 'wps-file'


def notify(summary, body=''):
    """发一条桌面通知（notify-send 不存在就静默跳过）。"""
    try:
        args = ['notify-send', '-a', 'WPS 最近文档复制',
                '-i', _notify_icon(), '-t', '3000', summary]
        if body:
            args.append(body)
        subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
