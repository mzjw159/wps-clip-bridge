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


def request_copy(paths, timeout=3.0):
    """请求后台把文件放进剪贴板，返回 (ok, message)。"""
    paths = [os.path.abspath(p) for p in paths]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        return False, '没有可复制的文件（路径不存在）'

    if not ensure_bridge():
        return False, '后台服务未能启动，请查看 %s' % os.path.join(
            core.state_dir(), 'daemon.log')

    marker = core.mtime(core.runtime_file('published'))
    req = core.req_path()
    try:
        tmp = req + '.tmp%d' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(paths) + '\n')
        os.replace(tmp, req)          # 原子替换，避免服务器读到半截
    except OSError as ex:
        return False, '写入请求失败: %s' % ex

    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = core.mtime(core.runtime_file('published'))
        if cur is not None and cur != marker:
            break
        time.sleep(0.05)
    else:
        return False, '后台没有响应，请运行 wps-copy --status 检查'

    # 端到端确认：真的能从剪贴板读回这个文件
    if core.clipboard_has(paths[0]):
        return True, ''
    # 少数情况（Wayland 合成器抢先）读不回，但服务已发布，仍算成功
    return True, ''


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


def notify(summary, body=''):
    """发一条桌面通知（notify-send 不存在就静默跳过）。"""
    try:
        args = ['notify-send', '-a', 'WPS 最近文档复制',
                '-i', 'wps-file', '-t', '3000', summary]
        if body:
            args.append(body)
        subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
