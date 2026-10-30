#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 debian/changelog 抽出某个版本的条目，生成 GitHub Release 的说明正文。

为什么要单独一个脚本：本地发版（tools/release.sh）和 CI 发版
（.github/workflows/release.yml）都得生成同一份说明。逻辑复制两份，
早晚会出现「本地发的和 CI 发的不一样」这种没法查的差异。

用法：
    tools/changelog-notes.py <版本> [-o 输出文件] [--deb 包名]

不给 -o 就打到标准输出。--deb 用来在安装命令里写对文件名，
默认 wps-clip-bridge_<版本>_all.deb。

退出码：0 成功；1 版本不存在或读不到 changelog。
"""
from __future__ import print_function

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(HERE, 'debian', 'changelog')

TEMPLATE_TAIL = """## 安装

```bash
sudo dpkg -i {deb}
```

卸载：`sudo dpkg -r wps-clip-bridge`

## 校验

```bash
sha256sum -c SHA256SUMS
```
"""


def sections(path):
    """把 changelog 切成 {版本: 段落正文}。

    用 split 而不是逐行状态机：changelog 的格式是 `包名 (版本) 发行版; 紧急度`
    起一行，条目里不会再出现这种行，所以按这个模式切是稳的。
    """
    raw = io.open(path, encoding='utf-8').read()
    parts = re.split(r'(?m)^\S+ \((\S+)\)', raw)
    # parts = [前言, 版本1, 正文1, 版本2, 正文2, ...]
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def build(version, deb):
    sec = sections(CHANGELOG)
    if version not in sec:
        raise SystemExit('debian/changelog 里没有 %s 这一段' % version)

    lines = [l.strip() for l in sec[version].splitlines()
             if l.strip() and not l.startswith(' -- ')]
    lines = lines[1:]                      # 去掉 "unstable; urgency=medium" 那行

    out = ['## 本次更新', '']
    for line in lines:
        if line.startswith('* '):
            out.append('- ' + line[2:])
        else:
            # changelog 的续行本来就带缩进，保留下来读着像段落
            out.append('  ' + line)
    out.append('')
    out.append(TEMPLATE_TAIL.format(deb=deb))
    return '\n'.join(out)


def main(argv):
    args = list(argv)
    version = None
    out_path = None
    deb = None
    while args:
        a = args.pop(0)
        if a in ('-h', '--help'):
            print(__doc__.strip())
            return 0
        elif a == '-o':
            out_path = args.pop(0)
        elif a == '--deb':
            deb = args.pop(0)
        elif version is None:
            version = a
        else:
            raise SystemExit('多余的参数：%s' % a)

    if not version:
        raise SystemExit('用法：changelog-notes.py <版本> [-o 文件] [--deb 包名]')
    if not os.path.isfile(CHANGELOG):
        raise SystemExit('找不到 %s（要在仓库里跑）' % CHANGELOG)

    text = build(version, deb or 'wps-clip-bridge_%s_all.deb' % version)
    if out_path:
        io.open(out_path, 'w', encoding='utf-8').write(text)
        print('%s（%d 行）' % (out_path, text.count('\n')))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
