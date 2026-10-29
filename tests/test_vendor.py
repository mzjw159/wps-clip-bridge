# -*- coding: utf-8 -*-
"""内置依赖（_vendor）的自检。

这里守的是「别人的机器上没有 python3-xlib 也能跑」这件事。它有两个失效方式，
而且两种都只会在**别人的机器上**才暴露：

1. `src/wpsclip/_vendor/` 被误删 / 忘了随包分发 —— 打包后仍然「能装」，但一运行
   就 ImportError；
2. `wpsclip/__init__.py` 里往 sys.path 追加 _vendor 的那几行没了 —— 本机因为装过
   python3-xlib，照样一切正常，谁也不会发现。

所以既检查文件在不在，也真的在「看不到系统包」的子进程里跑一次导入。
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
PKG = os.path.join(SRC, 'wpsclip')
VENDOR = os.path.join(PKG, '_vendor')


class VendorLayoutTest(unittest.TestCase):
    def test_vendor_dir_exists(self):
        self.assertTrue(os.path.isdir(VENDOR),
                        '缺少 src/wpsclip/_vendor/（内置 python-xlib）')

    def test_xlib_files_present(self):
        for rel in ('Xlib/__init__.py', 'Xlib/display.py',
                    'Xlib/protocol/display.py', 'Xlib/XK.py', 'six.py'):
            self.assertTrue(os.path.isfile(os.path.join(VENDOR, rel)),
                            '内置依赖缺少 %s' % rel)

    def test_vendor_registered_in_sys_path(self):
        sys.path.insert(0, SRC)
        try:
            import wpsclip
        finally:
            sys.path.remove(SRC)
        self.assertIn(VENDOR, sys.path,
                      '__init__.py 没把 _vendor 加进 sys.path')

    def test_vendor_comes_after_system(self):
        """系统装了 python3-xlib 时必须优先用系统的（那份有发行版补丁）。"""
        sys.path.insert(0, SRC)
        try:
            import wpsclip
        finally:
            sys.path.remove(SRC)
        idx = sys.path.index(VENDOR)
        # 追加在末尾：系统 site-packages 都在它前面
        self.assertEqual(idx, len(sys.path) - 1,
                         '_vendor 应该是 append 到末尾，不能 insert(0)')


class ImportWithoutSystemXlibTest(unittest.TestCase):
    """在子进程里把系统 site-packages 摘掉，确认还能 import。"""

    CODE = (
        'import sys\n'
        # 只摘掉 dist-packages / site-packages，stdlib 必须留着
        'sys.path = [p for p in sys.path '
        'if "dist-packages" not in p and "site-packages" not in p]\n'
        'sys.path.insert(0, %r)\n'
        'import wpsclip\n'
        'from Xlib import X, XK, display\n'
        'assert "dist-packages" not in display.__file__ or '
        '"_vendor" in display.__file__, display.__file__\n'
        'print(display.__file__)\n'
        'print(XK.string_to_keysym("c"))\n'
        % SRC
    )

    def test_import_works(self):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)      # 别让外部路径干扰结论
        r = subprocess.run([sys.executable, '-c', self.CODE],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0,
                         '没有系统 python3-xlib 时导入失败：\n%s'
                         % r.stderr.decode('utf-8', 'replace'))
        out = r.stdout.decode('utf-8', 'replace')
        self.assertIn('_vendor', out, '用到的不是内置那份：%s' % out)
        self.assertIn('99', out)          # keysym('c') == 99


if __name__ == '__main__':
    unittest.main()
