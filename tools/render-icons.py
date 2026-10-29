#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""从 data/wps-file.svg 渲染各尺寸 PNG 图标。

    ./tools/render-icons.py            # 渲染全套并打印内容占比
    ./tools/render-icons.py --check    # 只检查现有 PNG 的占比，不改文件

换图标的流程就是：把新 SVG 覆盖成 data/wps-file.svg，跑这个脚本，
再 ./build.sh 打包。详见 README 的「换图标」一节。

内容占比（trim 后宽度 / 画布宽度）要和系统图标对齐：workbuddy 是 40/48
≈ 83%。占比 100% 的图标在面板和任务栏里会显得比邻居大一圈 —— 原因是
SVG 里的图形铺满了整个 viewBox，而不是画布本身的问题。
"""
import os
import sys

import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, 'data', 'wps-file.svg')
ICON_DIR = os.path.join(ROOT, 'data', 'icons', 'hicolor')
# data/wps-file.png 是给「桌面/文件管理器直接引用」用的单图，保持 500px
MAIN_PNG = os.path.join(ROOT, 'data', 'wps-file.png')
SIZES = (48, 64, 128, 256, 512)
MAIN_SIZE = 500
TARGET_PERCENT = 83


def render(svg, size, out):
    """把 SVG 按 size 渲染成 PNG（SVG loader 支持按目标尺寸矢量渲染）。"""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(svg, size, size, True)
    pb.savev(out, 'png', [], [])
    return pb


def content_ratio(png):
    """非透明区域宽度占画布的比例（%）。"""
    pb = GdkPixbuf.Pixbuf.new_from_file(png)
    w, h = pb.get_width(), pb.get_height()
    if not pb.get_has_alpha():
        return 100
    n, stride = pb.get_n_channels(), pb.get_rowstride()
    data = pb.get_pixels()
    lo, hi = w, -1
    for y in range(0, h, max(1, h // 128)):        # 采样行，够判断边界
        base = y * stride
        for x in range(w):
            if data[base + x * n + 3] > 8:
                if x < lo:
                    lo = x
                if x > hi:
                    hi = x
    if hi < 0:
        return 0
    return round((hi - lo + 1) * 100.0 / w)


def main(argv):
    check_only = '--check' in argv
    if not os.path.exists(SVG):
        sys.stderr.write('找不到 %s\n' % SVG)
        return 1

    targets = []
    for sz in SIZES:
        d = os.path.join(ICON_DIR, '%dx%d' % (sz, sz), 'apps')
        targets.append((sz, os.path.join(d, 'wps-file.png'), d))
    targets.append((MAIN_SIZE, MAIN_PNG, os.path.dirname(MAIN_PNG)))

    if not check_only:
        for sz, out, d in targets:
            if not os.path.isdir(d):
                os.makedirs(d)
            render(SVG, sz, out)

    print('%-6s %-8s %s' % ('尺寸', '内容占比', '文件'))
    bad = 0
    for sz, out, _d in targets:
        if not os.path.exists(out):
            print('%-6d %-8s %s（缺失）' % (sz, '-', out))
            bad += 1
            continue
        r = content_ratio(out)
        flag = '✔' if abs(r - TARGET_PERCENT) <= 8 else '✘ 偏离目标 %d%%' % TARGET_PERCENT
        print('%-6d %-8s %s  %s' % (sz, '%d%%' % r, os.path.relpath(out, ROOT), flag))
        if abs(r - TARGET_PERCENT) > 8:
            bad += 1
    return 0 if not bad else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
