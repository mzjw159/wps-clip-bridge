#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""入口脚本共用的「友好启动」。

直接用 ``dpkg -i`` 装包时 apt 不会补依赖，缺 ``python3-xlib`` / ``python3-gi``
只会甩一段英文 traceback。别人（尤其是不熟 Linux 的同事）看到那个只会来问你
「装完了打不开」。这里把 ImportError 翻成一句人话 + 一行可以直接复制的命令。
"""
import importlib
import sys

DEPS_HINT = ('缺少运行依赖（%s）。\n'
             '正常双击安装包或用 apt 安装时不会出现这种情况，多半是用 dpkg -i\n'
             '装了却没补依赖。执行：\n'
             '    sudo apt install -y python3-gi gir1.2-gtk-3.0\n'
             '或者直接重装一次安装包（apt 会把依赖一起拉齐）：\n'
             '    sudo apt install ./wps-clip-bridge_*_all.deb\n'
             '注：python-xlib 已内置在本程序里，无需另外安装。\n')


def run(module_name, func_name='main', argv=None):
    """导入模块并调用它的入口函数，缺依赖时返回 2 而不是抛 traceback。"""
    try:
        mod = importlib.import_module(module_name)
    except ImportError as ex:
        sys.stderr.write(DEPS_HINT % ex)
        return 2
    return getattr(mod, func_name)(argv)
