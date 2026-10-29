# -*- coding: utf-8 -*-
"""gui 里的纯函数：来源判断、类型归类、时间分桶、大小格式化。

这些函数决定列表里每一行长什么样，改错了界面会「看起来正常但内容不对」，
比崩溃更难发现，所以逐个钉住。不需要显示器，也不创建任何窗口。
"""
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault('GDK_BACKEND', 'x11')

try:
    from wpsclip import gui
    _IMPORT_ERR = None
except Exception as _ex:      # 没装 PyGObject 的环境跳过，而不是报错
    gui = None
    _IMPORT_ERR = _ex

_need_gi = unittest.skipIf(gui is None, '需要 PyGObject（gi）')


@_need_gi
class SourceOfTest(unittest.TestCase):
    def test_cloud(self):
        self.assertEqual(gui.source_of('/home/u/WPS Cloud Files/a.docx'), '云端')
        self.assertEqual(gui.source_of('/home/u/wpscloud/a.docx'), '云端')
        self.assertEqual(gui.source_of('/home/u/云文档/a.docx'), '云端')

    def test_wechat(self):
        self.assertEqual(gui.source_of('/home/u/xwechat_files/a.pdf'), '微信')
        self.assertEqual(gui.source_of('/home/u/WeChat Files/a.pdf'), '微信')
        self.assertEqual(gui.source_of('/home/u/微信/a.pdf'), '微信')

    def test_local_default(self):
        self.assertEqual(gui.source_of('/home/u/Desktop/a.docx'), '本地')
        self.assertEqual(gui.source_of('/tmp/a.xlsx'), '本地')

    def test_cloud_wins_over_wechat(self):
        # 路径里同时出现两者时，云端的判断在前，结果应为云端
        self.assertEqual(
            gui.source_of('/home/u/WPS Cloud Files/wechat/a.docx'), '云端')


@_need_gi
class TypeLabelTest(unittest.TestCase):
    def test_categories(self):
        cases = {
            '.doc': '文档', '.docx': '文档', '.wps': '文档', '.rtf': '文档',
            '.xls': '表格', '.xlsx': '表格', '.et': '表格', '.csv': '表格',
            '.ppt': '演示', '.pptx': '演示', '.dps': '演示',
            '.pdf': 'PDF', '.ofd': 'PDF',
            '.txt': '文本', '.md': '文本',
        }
        for ext, want in cases.items():
            self.assertEqual(gui.type_label('/a' + ext), want, ext)

    def test_case_insensitive(self):
        self.assertEqual(gui.type_label('/A.DOCX'), '文档')

    def test_unknown_and_no_ext(self):
        self.assertEqual(gui.type_label('/a.xyz'), '其他')
        self.assertEqual(gui.type_label('/noext'), '其他')


@_need_gi
class TypeIconTest(unittest.TestCase):
    def test_icons(self):
        cases = {
            '.docx': 'x-office-document',
            '.xlsx': 'x-office-spreadsheet',
            '.pptx': 'x-office-presentation',
            '.pdf': 'application-pdf',
            '.png': 'image-x-generic',
            '.txt': 'text-x-generic',
        }
        for ext, want in cases.items():
            self.assertEqual(gui.type_icon('/a' + ext), want, ext)

    def test_fallback_icon_is_never_empty(self):
        for name in ('/a.xyz', '/noext', '/.hidden'):
            self.assertTrue(gui.type_icon(name))


@_need_gi
class BucketOfTest(unittest.TestCase):
    DAY = 86400

    def idx(self, age_seconds):
        return gui.bucket_of(time.time() - age_seconds)[0]

    def test_zero_mtime_is_oldest(self):
        self.assertEqual(gui.bucket_of(0)[0], 5)
        self.assertEqual(gui.bucket_of(0)[1], '更早')

    def test_today(self):
        self.assertEqual(self.idx(0), 0)
        self.assertEqual(self.idx(3600), 0)

    def test_yesterday(self):
        self.assertEqual(self.idx(self.DAY), 1)

    def test_within_3_days(self):
        self.assertEqual(self.idx(2 * self.DAY), 2)
        self.assertEqual(self.idx(3 * self.DAY), 2)

    def test_within_7_days(self):
        self.assertEqual(self.idx(4 * self.DAY), 3)
        self.assertEqual(self.idx(7 * self.DAY), 3)

    def test_within_30_days(self):
        self.assertEqual(self.idx(8 * self.DAY), 4)
        self.assertEqual(self.idx(30 * self.DAY), 4)

    def test_older(self):
        self.assertEqual(self.idx(31 * self.DAY), 5)
        self.assertEqual(self.idx(400 * self.DAY), 5)

    def test_index_matches_label_table(self):
        for age in (0, self.DAY, 3 * self.DAY, 7 * self.DAY,
                    30 * self.DAY, 400 * self.DAY):
            idx, label = gui.bucket_of(time.time() - age)
            self.assertEqual(label, gui._BUCKETS[idx])

    def test_buckets_are_monotonic(self):
        # 时间越久，桶序号不能变小（顺序错了分组会乱）
        prev = -1
        for age in (0, self.DAY, 3 * self.DAY, 7 * self.DAY,
                    30 * self.DAY, 400 * self.DAY):
            cur = self.idx(age)
            self.assertGreaterEqual(cur, prev)
            prev = cur


@_need_gi
class HumanSizeTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(gui.human_size(0), '0 B')
        self.assertEqual(gui.human_size(512), '512 B')
        self.assertEqual(gui.human_size(1023), '1023 B')

    def test_kb_mb_gb(self):
        self.assertEqual(gui.human_size(1024), '1.0 KB')
        self.assertEqual(gui.human_size(1536), '1.5 KB')
        self.assertEqual(gui.human_size(1024 ** 2), '1.0 MB')
        self.assertEqual(gui.human_size(int(1.5 * 1024 ** 2)), '1.5 MB')
        self.assertEqual(gui.human_size(1024 ** 3), '1.0 GB')

    def test_beyond_gb_does_not_crash(self):
        self.assertEqual(gui.human_size(1024 ** 4), '1024.0 GB')


if __name__ == '__main__':
    if _IMPORT_ERR is not None:
        print('跳过：无法导入 gui（%r）' % (_IMPORT_ERR,))
    unittest.main()
