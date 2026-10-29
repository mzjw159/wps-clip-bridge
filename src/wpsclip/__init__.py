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
import os as _os
import sys as _sys

# 兜底把 python-xlib 打进包里（_vendor/Xlib，LGPLv2+，见 debian/copyright）。
#
# 为什么：整个包唯一的「非桌面必备」依赖就是 python3-xlib，而别人双击 .deb 时
# 安装器要不要帮你从软件源拉这个依赖、拉不拉得动，完全取决于他那台机器的源配置
# 和网络。装完打不开是最难解释的失败。把它塞进包里，安装就变成零依赖问题。
#
# 追加到 sys.path 末尾：系统里装了 python3-xlib 就用系统的（版本可能更新、
# 跟发行版打过补丁），没装才用包里这份。
_VENDOR = _os.path.join(_os.path.dirname(__file__), '_vendor')
if _os.path.isdir(_VENDOR) and _VENDOR not in _sys.path:
    _sys.path.append(_VENDOR)

from .core import VERSION as __version__  # noqa: E402

__all__ = ['__version__']
