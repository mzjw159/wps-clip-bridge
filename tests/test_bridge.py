# -*- coding: utf-8 -*-
"""剪贴板桥的纯逻辑：URI 列表解析与合法性判定。

这两个函数决定「从别处复制文件」这条路通不通，出错的症状很隐蔽
（剪贴板看着有内容，粘贴却是灰的），所以单独测。
不需要 X 连接 —— uri_list_valid 用替身对象喂数据。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from wpsclip.bridge import Bridge
    _IMPORT_ERR = None
except Exception as _ex:      # 没装 python3-xlib 时跳过
    Bridge = None
    _IMPORT_ERR = _ex

_need_xlib = unittest.skipIf(Bridge is None, '需要 python3-xlib')


@_need_xlib
class ParseUrisTest(unittest.TestCase):
    def parse(self, text):
        return Bridge.parse_uris(text)

    def test_single_uri(self):
        self.assertEqual(self.parse('file:///home/u/a.docx'), ['/home/u/a.docx'])

    def test_percent_decoding_space(self):
        self.assertEqual(self.parse('file:///home/u/a%20b.docx'),
                         ['/home/u/a b.docx'])

    def test_percent_decoding_chinese(self):
        uri = 'file:///home/u/%E6%8A%A5%E5%91%8A.docx'
        self.assertEqual(self.parse(uri), ['/home/u/报告.docx'])

    def test_plain_absolute_paths(self):
        self.assertEqual(self.parse('/home/u/a.docx\n/home/u/b.xlsx'),
                         ['/home/u/a.docx', '/home/u/b.xlsx'])

    def test_gnome_copy_header_skipped(self):
        text = 'copy\nfile:///a.docx\nfile:///b.docx\n'
        self.assertEqual(self.parse(text), ['/a.docx', '/b.docx'])

    def test_gnome_cut_header_skipped(self):
        self.assertEqual(self.parse('cut\nfile:///a.docx\n'), ['/a.docx'])

    def test_crlf_line_endings(self):
        self.assertEqual(self.parse('file:///a.docx\r\nfile:///b.docx\r\n'),
                         ['/a.docx', '/b.docx'])

    def test_comment_lines_skipped(self):
        self.assertEqual(self.parse('# a comment\nfile:///a.docx'), ['/a.docx'])

    def test_blank_lines_skipped(self):
        self.assertEqual(self.parse('\n\n  \nfile:///a.docx\n\n'), ['/a.docx'])

    def test_duplicates_removed_in_order(self):
        self.assertEqual(self.parse('file:///a\nfile:///b\nfile:///a'),
                         ['/a', '/b'])

    def test_non_file_scheme_ignored(self):
        self.assertEqual(self.parse('https://example.com/a\nfile:///a.docx'),
                         ['/a.docx'])

    def test_empty_inputs(self):
        self.assertEqual(self.parse(''), [])
        self.assertEqual(self.parse(None), [])


class _FakeBridge(object):
    """只用来喂 read_text，不碰 X 连接。"""

    def __init__(self, text):
        self.text = text

    def read_text(self, _name):
        return self.text


@_need_xlib
class UriListValidTest(unittest.TestCase):
    URI = {'text/uri-list'}

    def valid(self, text, names=None):
        return Bridge.uri_list_valid(_FakeBridge(text), names or self.URI)

    def test_plain_uri_list_is_valid(self):
        self.assertTrue(self.valid('file:///a.docx\r\nfile:///b.xlsx\r\n'))

    def test_single_line_valid(self):
        self.assertTrue(self.valid('file:///a.docx'))

    def test_missing_target_is_invalid(self):
        self.assertFalse(self.valid('file:///a.docx',
                                    {'x-special/gnome-copied-files'}))

    def test_copy_header_inside_uri_list_is_invalid(self):
        # 有些代理把 gnome 格式原样塞进 text/uri-list，桌面端解析会失败
        self.assertFalse(self.valid('copy\nfile:///a.docx\n'))

    def test_cut_header_inside_uri_list_is_invalid(self):
        self.assertFalse(self.valid('cut\nfile:///a.docx\n'))

    def test_empty_text_is_invalid(self):
        self.assertFalse(self.valid(''))
        self.assertFalse(self.valid('   \n  '))

    def test_none_text_is_invalid(self):
        self.assertFalse(self.valid(None))

    def test_garbage_line_is_invalid(self):
        self.assertFalse(self.valid('file:///a.docx\nnot-a-path\n'))

    def test_comment_only_is_valid_per_spec(self):
        # uri-list 规范允许注释行；没有别的非法行就算合法
        self.assertTrue(self.valid('# note\nfile:///a.docx\n'))


if __name__ == '__main__':
    if _IMPORT_ERR is not None:
        print('跳过：无法导入 bridge（%r）' % (_IMPORT_ERR,))
    unittest.main()
