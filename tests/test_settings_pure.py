# -*- coding: utf-8 -*-
"""设置窗口里可以脱离界面验证的部分：链接颜色、内容区高度上限。

这两个都是「挑颜色/挑尺寸」的决定，写错了肉眼可能一时看不出（浅色主题上
深蓝色写成浅蓝就是白底看不清），所以钉住。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault('GDK_BACKEND', 'x11')

try:
    from wpsclip import settings
    _IMPORT_ERR = None
except Exception as _ex:      # 没装 PyGObject 的环境跳过
    settings = None
    _IMPORT_ERR = _ex

_need_gi = unittest.skipIf(settings is None, '需要 PyGObject（gi）')


@_need_gi
class LinkColorTest(unittest.TestCase):
    def test_light_and_dark_differ(self):
        light = settings.LINK_COLORS[False]
        dark = settings.LINK_COLORS[True]
        self.assertNotEqual(light, dark)

    def test_light_is_dark_enough(self):
        # 浅色主题上链接必须足够深（白底上 #125a9e 的对比度约 6:1）
        hexv = settings.LINK_COLORS[False].lstrip('#')
        r, g, b = (int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        self.assertLess(luma, 110)

    def test_dark_is_light_enough(self):
        hexv = settings.LINK_COLORS[True].lstrip('#')
        r, g, b = (int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        self.assertGreater(luma, 140)

    def test_follows_config(self):
        old = settings.core.conf
        try:
            settings.core.conf = lambda *a, **k: 'light'
            self.assertEqual(settings.link_color(), settings.LINK_COLORS[False])
            settings.core.conf = lambda *a, **k: 'dark'
            self.assertEqual(settings.link_color(), settings.LINK_COLORS[True])
        finally:
            settings.core.conf = old


@_need_gi
class MaxHeightTest(unittest.TestCase):
    def test_within_bounds(self):
        h = settings.content_max_height()
        self.assertGreaterEqual(h, 320)
        self.assertLessEqual(h, 560)

    def test_smaller_than_screen(self):
        # 内容区上限要比屏幕矮一截，否则底部按钮又被顶出去
        try:
            from gi.repository import Gdk
            screen = Gdk.Screen.get_default()
            sh = screen.get_height() if screen is not None else None
        except Exception:
            sh = None
        if not sh:
            self.skipTest('没有显示器')
        self.assertLess(settings.content_max_height(), sh)


if __name__ == '__main__':
    unittest.main()
