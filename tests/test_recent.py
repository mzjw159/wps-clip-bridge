# -*- coding: utf-8 -*-
"""WPS 最近文档解析。

重点保护 unesc()：``\\x`` 后跟 **2~4 位**十六进制这个规则是实测算出来的，
WPS 把每个 ASCII 字符也逐字转义（"2026" 写成 \\x32\\x30\\x32\\x36），
若按定长 4 位解析，整条路径就废了 —— 这类回归必须被测试挡住。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wpsclip import recent  # noqa: E402


class UnescTest(unittest.TestCase):
    def test_ascii_written_as_escapes(self):
        self.assertEqual(recent.unesc(r'a\x32\x30\x32\x36.txt'), 'a2026.txt')

    def test_cjk_four_digit_points(self):
        self.assertEqual(recent.unesc(r'\x684c\x9762'), '桌面')

    def test_three_digit_point(self):
        self.assertEqual(recent.unesc(r'\x4e2'), chr(0x4e2))

    def test_stops_at_first_non_hex(self):
        self.assertEqual(recent.unesc(r'\x41z'), 'Az')

    def test_invalid_escape_kept_verbatim(self):
        self.assertEqual(recent.unesc(r'\xZZ'), r'\xZZ')

    def test_escape_at_end_without_digits(self):
        self.assertEqual(recent.unesc(r'abc\x'), r'abc\x')

    def test_double_backslash_collapses(self):
        self.assertEqual(recent.unesc(r'a\\b'), 'a\\b')

    def test_lone_trailing_backslash(self):
        self.assertEqual(recent.unesc('abc\\'), 'abc\\')

    def test_mixed_real_world_path(self):
        raw = r'/home/u/\x684c\x9762/\x62a5\x544a2026.docx'
        self.assertEqual(recent.unesc(raw), '/home/u/桌面/报告2026.docx')

    def test_plain_path_unchanged(self):
        self.assertEqual(recent.unesc('/home/u/报告.docx'), '/home/u/报告.docx')


class CollectTest(unittest.TestCase):
    def test_local_recent_files(self):
        data = 'wps\\RecentFiles\\files\\1\\path=/home/u/a.docx\n'
        self.assertEqual(list(recent._collect(data)), ['/home/u/a.docx'])

    def test_each_office_app_prefix(self):
        data = ''.join('%s\\RecentFiles\\files\\1\\path=/home/u/%s.docx\n' % (p, p)
                       for p in ('wps', 'et', 'wpp', 'pdf', 'ofd'))
        found = recent._collect(data)
        self.assertEqual(len(found), 5)
        self.assertIn('/home/u/wps.docx', found)

    def test_cloud_open_file_list(self):
        data = ('plugins\\officespace\\setting\\openfilelist\\1\\path='
                '/home/u/cloud.docx\n')
        self.assertIn('/home/u/cloud.docx', recent._collect(data))

    def test_cloud_localpath_variant(self):
        data = ('plugins\\officespace\\x\\openfilelist\\2\\localpath='
                '/home/u/cached.docx\n')
        self.assertIn('/home/u/cached.docx', recent._collect(data))

    def test_dedup_same_path(self):
        data = ('wps\\RecentFiles\\files\\1\\path=/a.docx\n'
                'wps\\RecentFiles\\files\\2\\path=/a.docx\n')
        self.assertEqual(list(recent._collect(data)), ['/a.docx'])

    def test_relative_path_rejected(self):
        data = 'wps\\RecentFiles\\files\\1\\path=relative/a.docx\n'
        self.assertEqual(recent._collect(data), {})

    def test_unknown_app_prefix_ignored(self):
        data = 'notepad\\RecentFiles\\files\\1\\path=/a.txt\n'
        self.assertEqual(recent._collect(data), {})


class ParseRecentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpsrecent_')
        self.conf = os.path.join(self.tmp, 'Office.conf')
        self._orig = recent.CONF
        recent.CONF = self.conf

    def tearDown(self):
        recent.CONF = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name, age):
        p = os.path.join(self.tmp, name)
        with open(p, 'w') as f:
            f.write('x')
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def _write_conf(self, paths):
        with open(self.conf, 'w', encoding='utf-8') as f:
            for i, p in enumerate(paths, 1):
                f.write('wps\\RecentFiles\\files\\%d\\path=%s\n' % (i, p))

    def test_sorted_by_mtime_desc(self):
        old = self._make('old.docx', 100)
        new = self._make('new.docx', 10)
        self._write_conf([old, new])
        self.assertEqual(recent.parse_recent(), [new, old])

    def test_missing_files_filtered_by_default(self):
        alive = self._make('alive.docx', 10)
        self._write_conf([alive, os.path.join(self.tmp, 'gone.docx')])
        self.assertEqual(recent.parse_recent(), [alive])

    def test_only_exist_false_keeps_missing(self):
        alive = self._make('alive.docx', 10)
        self._write_conf([alive, os.path.join(self.tmp, 'gone.docx')])
        self.assertEqual(len(recent.parse_recent(only_exist=False)), 2)

    def test_limit_applied(self):
        paths = [self._make('f%d.docx' % i, i * 10) for i in range(5)]
        self._write_conf(paths)
        self.assertEqual(len(recent.parse_recent(limit=2)), 2)

    def test_no_conf_returns_empty(self):
        recent.CONF = os.path.join(self.tmp, 'does-not-exist')
        self.assertEqual(recent.parse_recent(), [])

    def test_empty_conf_returns_empty(self):
        with open(self.conf, 'w') as f:
            f.write('')
        self.assertEqual(recent.parse_recent(), [])

    def test_escaped_path_in_conf_is_decoded(self):
        p = self._make('报告.docx', 10)
        raw = ''.join('\\x%x' % ord(ch) for ch in p)
        with open(self.conf, 'w') as f:
            f.write('wps\\RecentFiles\\files\\1\\path=%s\n' % raw)
        self.assertEqual(recent.parse_recent(), [p])


class KindOfTest(unittest.TestCase):
    def test_cloud(self):
        self.assertEqual(recent.kind_of('/home/u/WPS Cloud Files/a.docx'), '云')

    def test_document(self):
        for ext in ('.doc', '.docx', '.wps', '.wpt', '.rtf'):
            self.assertEqual(recent.kind_of('/a' + ext), 'W')

    def test_sheet(self):
        for ext in ('.xls', '.xlsx', '.et', '.ett', '.csv'):
            self.assertEqual(recent.kind_of('/a' + ext), 'X')

    def test_slide(self):
        for ext in ('.ppt', '.pptx', '.dps', '.dpt'):
            self.assertEqual(recent.kind_of('/a' + ext), 'P')

    def test_pdf(self):
        for ext in ('.pdf', '.ofd'):
            self.assertEqual(recent.kind_of('/a' + ext), 'D')

    def test_unknown(self):
        self.assertEqual(recent.kind_of('/a.xyz'), '·')
        self.assertEqual(recent.kind_of('/noext'), '·')


if __name__ == '__main__':
    unittest.main()
