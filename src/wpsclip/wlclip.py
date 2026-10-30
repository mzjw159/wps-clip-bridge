#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Wayland 剪贴板兜底：用 wl-copy 再往原生 Wayland 剪贴板写一份。

X11 那条路是 ``bridge.py`` 持有 CLIPBOARD selection。纯 Wayland 应用
（GTK4 原生、Qt6 的 wayland 后端）读的却是 ``wl_data_device``。XWayland
通常会把两边桥起来，但桥接对 ``text/uri-list`` 并不可靠 —— 结果是
「复制成功了，粘出来却是空的」或者「粘出来是一串路径文本」。

装了 ``wl-clipboard``（``wl-copy``）的话，就额外写一份到 Wayland 侧；
没装就静默跳过，X11 老路照常工作。

为什么不自己实现 Wayland 客户端：``wl_data_device`` 还要拿到 compositor
的 seat、data_device，再处理一堆协议往返，几十行 C 起步。``wl-copy`` 是个
二十来 KB 的小工具，装的人也多，调用它比重新实现一遍划算。
"""
import os
import shutil
import subprocess
import urllib.parse

from . import core

_wl_copy = None
_checked = False


def session_is_wayland():
    if os.environ.get('XDG_SESSION_TYPE', '').strip().lower() == 'wayland':
        return True
    return bool(os.environ.get('WAYLAND_DISPLAY', '').strip())


def wl_copy():
    """wl-copy 的绝对路径，没有则 None（只探测一次）。"""
    global _wl_copy, _checked
    if not _checked:
        _checked = True
        if session_is_wayland():
            _wl_copy = shutil.which('wl-copy')
    return _wl_copy


def available():
    return wl_copy() is not None


def reset():
    """测试用：忘掉探测结果，下次重新找。"""
    global _wl_copy, _checked
    _wl_copy = None
    _checked = False


def uri_list(paths):
    # 规范要求 CRLF，末尾也要有一个 —— 少一个末尾 CRLF 时部分程序会吞掉最后一行
    return '\r\n'.join('file://' + urllib.parse.quote(p, safe='/')
                       for p in paths) + '\r\n'


def copy_files(paths, mode='copy'):
    """把文件列表写进 Wayland 剪贴板，返回是否成功。

    ``mode`` 只影响 X11 那边（``x-special/gnome-copied-files`` 的 copy/cut
    首行）；Wayland 的 ``text/uri-list`` 没有「剪切」这个概念，一律按复制处理。
    """
    exe = wl_copy()
    paths = [p for p in paths if p]
    if not exe or not paths:
        return False
    try:
        log_path = os.path.join(core.state_dir(), 'wl-copy.log')
        log = open(log_path, 'ab')
    except OSError:
        log = subprocess.DEVNULL
    try:
        proc = subprocess.Popen([exe, '--type', 'text/uri-list'],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=log)
    except Exception as ex:
        core.log('启动 wl-copy 失败: %r' % (ex,))
        return False
    finally:
        if log not in (subprocess.DEVNULL, None):
            try:
                log.close()
            except Exception:
                pass
    try:
        proc.communicate(uri_list(paths).encode('utf-8'), timeout=3)
    except subprocess.TimeoutExpired:
        # wl-copy 会 fork 到后台继续持有 selection，主进程迟迟不退说明
        # 合成器没接（比如在纯 X11 会话里误调），别把调用方拖住。
        try:
            proc.kill()
        except Exception:
            pass
        core.log('wl-copy 超时（合成器没响应？）')
        return False
    except Exception as ex:
        core.log('wl-copy 通信失败: %r' % (ex,))
        return False
    if proc.returncode != 0:
        core.log('wl-copy 退出码 %s（详见 %s）'
                 % (proc.returncode, os.path.join(core.state_dir(), 'wl-copy.log')))
        return False
    core.log('Wayland 剪贴板已写入 %d 个路径（wl-copy）' % len(paths))
    return True
