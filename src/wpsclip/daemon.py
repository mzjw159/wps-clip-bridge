#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""后台守护：拉起并看护「剪贴板服务」和「全局热键」两个子进程。

登录自启（``/etc/xdg/autostart``）启动的就是它。子进程异常退出会自动重启，
连续崩溃则退避，避免刷日志烧 CPU。
"""
import os
import signal
import subprocess
import sys
import time

from . import core

BACKOFF_MIN = 2.0
BACKOFF_MAX = 30.0
# 子进程存活超过这个时间才认为「启动成功」，重启计数清零
STABLE_AFTER = 20.0

CHILDREN = (
    # (名字, 安装路径, 包内模块名)
    ('bridge', '/usr/bin/wps-clip-bridge', 'wpsclip.bridge'),
    ('hotkey', '/usr/bin/wps-copy-hotkey', 'wpsclip.hotkey'),
)

_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def _wait_display(timeout=25.0):
    """等 X 就绪再起子进程。

    登录自启可能比合成器更早执行，此时 DISPLAY 连不上。与其让子进程反复
    崩溃重启，不如在这里安静等一会儿。
    """
    if not os.environ.get('DISPLAY'):
        # 从常见的 X socket 里挑一个
        for n in ('0', '1'):
            if os.path.exists('/tmp/.X11-unix/X' + n):
                os.environ['DISPLAY'] = ':' + n
                break
    if not os.environ.get('DISPLAY'):
        core.log('没有 DISPLAY 环境变量，跳过等待')
        return False
    deadline = time.time() + timeout
    try:
        from Xlib import display
    except ImportError:
        core.log('缺少 python3-xlib，无法校验显示服务')
        return False
    while time.time() < deadline and not _stop:
        try:
            d = display.Display()
            d.close()
            core.log('显示服务已就绪：%s' % os.environ['DISPLAY'])
            return True
        except Exception:
            time.sleep(0.5)
    core.log('等待显示服务超时（%s），仍然继续启动' % os.environ.get('DISPLAY'))
    return False


class Child(object):
    def __init__(self, name, exe, module):
        self.name = name
        self.exe = exe
        self.module = module
        self.proc = None
        self.started = 0.0
        self.fails = 0
        self.next_try = 0.0

    def _cmd(self):
        if os.path.exists(self.exe):
            return [self.exe]
        return [sys.executable, '-m', self.module]

    def spawn(self):
        try:
            out = open(os.path.join(core.state_dir(), self.name + '.log'), 'ab')
            self.proc = subprocess.Popen(
                self._cmd(), stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, close_fds=True)
            self.started = time.time()
            core.log('已启动 %s pid=%d' % (self.name, self.proc.pid))
        except Exception as ex:
            core.log('启动 %s 失败: %r' % (self.name, ex))
            self.proc = None
            self._backoff()

    def _backoff(self):
        self.fails += 1
        delay = min(BACKOFF_MIN * (2 ** (self.fails - 1)), BACKOFF_MAX)
        self.next_try = time.time() + delay
        core.log('%s 将在 %.0f 秒后重试（连续失败 %d 次）'
                 % (self.name, delay, self.fails))

    def tick(self):
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                if self.fails and time.time() - self.started > STABLE_AFTER:
                    self.fails = 0
                return
            core.log('%s 退出，返回码 %s' % (self.name, code))
            self.proc = None
            self._backoff()
            return

        if time.time() >= self.next_try:
            self.spawn()

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def main(argv=None):
    # 热键可以在配置里关掉
    kids = []
    for name, exe, module in CHILDREN:
        if name == 'hotkey' and not core.conf_bool('hotkey', 'enabled', True):
            core.log('配置禁用了热键，跳过')
            continue
        kids.append(Child(name, exe, module))

    si = core.SingleInstance('daemon')
    if not si.acquire():
        core.log('另一个守护进程已在运行，本次退出')
        return 0

    core.set_log_file(os.path.join(core.state_dir(), 'daemon.log'))
    core.log('=' * 56)
    core.log('%s %s 守护进程启动 pid=%d' % (core.APP, core.VERSION, os.getpid()))

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except Exception:
        pass

    _wait_display()

    try:
        while not _stop:
            for c in kids:
                c.tick()
            time.sleep(1.0)
    finally:
        core.log('收到退出信号，正在停止子进程…')
        for c in kids:
            c.stop()
        si.release()
        core.log('守护进程已退出')
    return 0


if __name__ == '__main__':
    sys.exit(main())
