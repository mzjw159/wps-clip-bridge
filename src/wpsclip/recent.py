#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""读取 WPS 的「最近打开文档」列表。

数据在 ``~/.config/Kingsoft/Office.conf``，形如::

    pdf\\RecentFiles\\files\\1\\path=/home/u/\\x684c\\x9762/\\x901a\\x77e5.pdf

转义规则（实测）：``\\x`` 后跟 **2~4 位**十六进制 Unicode 码点，遇到非十六进制
字符即停。不是定长 4 位 —— ASCII 字符（如文件名里的 "2026"）会写成
``\\x32\\x30\\x32\\x36``，若按定长 4 位解析会把 ``\\x32\\x30`` 吃成 0x3230，
整条路径就废了、文件被判为不存在而被过滤掉。
"""
import os
import re

CONF = os.path.expanduser('~/.config/Kingsoft/Office.conf')

_HEX = '0123456789abcdefABCDEF'

# 本地文档的最近打开记录
_PATH_RE = re.compile(
    r'^(?:wps|et|wpp|pdf|ofd)'
    r'\\RecentFiles\\files\\(\d+)\\(\w+)=(.*)$', re.M)

# 云文档 / 办公空间打开记录（打开过的云文件通常也在本地留了缓存副本）
_SPACE_RE = re.compile(
    r'^plugins\\officespace\\[^\n]*?\\openfilelist\\(\d+)\\(path|localpath)=(.*)$', re.M)


def unesc(s):
    """把 WPS 的 ``\\xHH[HH]`` 转义还原成真实字符串。"""
    out = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == '\\' and i + 1 < n and s[i + 1] == 'x':
            j = i + 2
            while j < n and j - (i + 2) < 4 and s[j] in _HEX:
                j += 1
            if j > i + 2:
                try:
                    out.append(chr(int(s[i + 2:j], 16)))
                    i = j
                    continue
                except ValueError:
                    pass
            out.append(s[i])
            i += 1
            continue
        if s[i] == '\\' and i + 1 < n and s[i + 1] == '\\':
            out.append('\\')
            i += 2
            continue
        out.append(s[i])
        i += 1
    return ''.join(out)


def _collect(data):
    """返回 {真实路径: 记录序号}，同路径只保留首次出现。"""
    found = {}
    groups = (_PATH_RE, _SPACE_RE)
    for pat in groups:
        for m in pat.finditer(data):
            raw = m.group(m.lastindex).strip()
            if not raw or '\n' in raw:
                continue
            path = unesc(raw)
            if not path.startswith('/'):
                continue
            if path not in found:
                found[path] = m.group(2) if pat is _PATH_RE else '0'
    return found


def parse_recent(limit=80, only_exist=True):
    """按文件修改时间倒序，返回最近文档的真实路径列表。"""
    try:
        with open(CONF, 'rb') as f:
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []

    rows = []
    for path in _collect(data):
        if only_exist and not os.path.isfile(path):
            continue
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = 0
        rows.append((mt, path))
    rows.sort(reverse=True)

    out, seen = [], set()
    for _, p in rows:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out[:limit]


def kind_of(path):
    """给列表用的类型标记：W/X/P/D/云。"""
    if 'WPS Cloud Files' in path:
        return '云'
    ext = os.path.splitext(path)[1].lower()
    return {
        '.doc': 'W', '.docx': 'W', '.wps': 'W', '.wpt': 'W', '.rtf': 'W',
        '.xls': 'X', '.xlsx': 'X', '.et': 'X', '.ett': 'X', '.csv': 'X',
        '.ppt': 'P', '.pptx': 'P', '.dps': 'P', '.dpt': 'P',
        '.pdf': 'D', '.ofd': 'D',
    }.get(ext, '·')


if __name__ == '__main__':
    for i, p in enumerate(parse_recent(), 1):
        print('%2d. %s' % (i, p))
