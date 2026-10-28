#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""wps-clip-bridge —— 让 WPS 里的文档能正常复制粘贴到桌面 / 微信。

模块划分::

    core.py     路径、配置、日志、单实例锁、剪贴板读取
    recent.py   解析 WPS 的最近文档记录
    bridge.py   常驻剪贴板服务（补救格式 + 代发文件）
    service.py  确保后台在跑、发起复制请求
    cli.py      命令行 wps-copy
    gui.py      图形窗口 wps-copy-gui
    hotkey.py   全局热键 wps-copy-hotkey
    daemon.py   看护进程 wps-clip-bridge-daemon
"""
from .core import VERSION as __version__

__all__ = ['__version__']
