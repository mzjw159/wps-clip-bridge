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
        # 关掉兜底数据源：否则「配置是空的」会被真实机器上的 xbel/云盘填满
        self._orig_xbel = recent.XBEL_CANDIDATES
        self._orig_cloud = recent.CLOUD_DIRS
        recent.XBEL_CANDIDATES = ()
        recent.CLOUD_DIRS = ()

    def tearDown(self):
        recent.CONF = self._orig
        recent.XBEL_CANDIDATES = self._orig_xbel
        recent.CLOUD_DIRS = self._orig_cloud
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


class ScanAssignmentsTest(unittest.TestCase):
    """通用扫描：不看键名，只看值 —— 这是「WPS 升级改了键名」时的唯一保命层。"""

    def test_catches_renamed_keys(self):
        data = ('wps\\RecentList\\items\\1\\file=/home/u/\\x62a5\\x544a.docx\n')
        self.assertEqual(recent._collect(data), {})          # 结构化落空
        self.assertEqual(recent.scan_assignments(data),
                         ['/home/u/报告.docx'])               # 通用扫描兜住

    def test_case_insensitive_structured_keys(self):
        data = 'wps\\recentfiles\\FILES\\2\\PATH=/home/u/b.xlsx\n'
        self.assertIn('/home/u/b.xlsx', recent._collect(data))

    def test_plain_absolute_path_value(self):
        self.assertEqual(recent.scan_assignments('k=/home/u/a.pdf\n'),
                         ['/home/u/a.pdf'])

    def test_file_uri_value(self):
        self.assertEqual(
            recent.scan_assignments('k=file:///home/u/%E6%8A%A5%E5%91%8A.docx\n'),
            ['/home/u/报告.docx'])

    def test_non_document_ignored(self):
        data = ('a=/home/u/x.txt\n'          # .txt 故意不算（配置里多是日志）
                'b=/home/u/archive.tar.gz\n'
                'c=/home/u/data.json\n')
        self.assertEqual(recent.scan_assignments(data), [])

    def test_wps_internal_paths_ignored(self):
        data = ('a=/home/u/.local/share/Kingsoft/office6/templates/wps/x.dotx\n'
                'b=/opt/Kingsoft/office6/cache/y.docx\n')
        self.assertEqual(recent.scan_assignments(data), [])

    def test_relative_and_empty_values(self):
        self.assertEqual(recent.scan_assignments('a=relative/x.docx\nb=\n'), [])

    def test_dedupes_repeated_value(self):
        data = 'a=/home/u/same.doc\nb=/home/u/same.doc\n'
        self.assertEqual(recent.scan_assignments(data), ['/home/u/same.doc'])

    def test_long_value_skipped(self):
        self.assertEqual(recent.scan_assignments('a=/' + 'x' * 5000 + '.docx\n'),
                         [])


class XbelTest(unittest.TestCase):
    def test_extracts_local_documents(self):
        xbel = ('<bookmark href="file:///home/u/%E6%8A%A5%E5%91%8A.docx" '
                'modified="2026-10-01T10:00:00Z" added="2026-10-01T09:00:00Z"/>')
        self.assertEqual(recent.parse_xbel(xbel), ['/home/u/报告.docx'])

    def test_skips_remote_and_non_documents(self):
        xbel = ('<bookmark href="http://a/b.docx" modified="x"/>'
                '<bookmark href="file:///home/u/a.tar.gz" modified="x"/>'
                '<bookmark href="file:///home/u/.local/share/Kingsoft/office6/x.docx"'
                ' modified="x"/>')
        self.assertEqual(recent.parse_xbel(xbel), [])

    def test_empty_and_garbage(self):
        for text in ('', None, '<xbel></xbel>', 'not xml at all'):
            self.assertEqual(recent.parse_xbel(text), [])


class CloudScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpscloud_')
        self._orig = recent.CLOUD_DIRS
        recent.CLOUD_DIRS = (self.tmp,)

    def tearDown(self):
        recent.CLOUD_DIRS = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, rel, age):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write('x')
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_returns_documents_newest_first(self):
        old = self._make('a/old.docx', 500)
        new = self._make('a/b/new.xlsx', 10)
        self.assertEqual(recent.scan_cloud_dirs(), [new, old])

    def test_skips_non_documents_and_dot_dirs(self):
        doc = self._make('keep.doc', 10)
        self._make('a/note.log', 5)
        self._make('.hidden/secret.docx', 1)
        self.assertEqual(recent.scan_cloud_dirs(), [doc])

    def test_missing_root_is_fine(self):
        recent.CLOUD_DIRS = (os.path.join(self.tmp, 'nope'),)
        self.assertEqual(recent.scan_cloud_dirs(), [])

    def test_respects_max_files(self):
        for i in range(5):
            self._make('f%d.docx' % i, i + 1)
        self.assertEqual(len(recent.scan_cloud_dirs(max_files=2)), 2)


class FallbackTest(unittest.TestCase):
    """主数据源坏了要能自动换源，而不是显示一个空列表。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpsfallback_')
        self._orig = (recent.CONF, recent.XBEL_CANDIDATES, recent.CLOUD_DIRS)
        recent.CONF = os.path.join(self.tmp, 'Office.conf')
        with open(os.path.join(self.tmp, 'Office.conf'), 'w') as f:
            f.write('wps\\SomethingNew\\1\\x=1\n')        # 完全没有路径
        self.doc = os.path.join(self.tmp, '云端.docx')
        with open(self.doc, 'w') as f:
            f.write('x')
        self.xbel = os.path.join(self.tmp, 'recently-used.xbel')
        with open(self.xbel, 'w', encoding='utf-8') as f:
            f.write('<bookmark href="file://%s" modified="x"/>' % self.doc)
        recent.XBEL_CANDIDATES = (self.xbel,)
        recent.CLOUD_DIRS = ()

    def tearDown(self):
        (recent.CONF, recent.XBEL_CANDIDATES,
         recent.CLOUD_DIRS) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_uses_xbel_when_conf_yields_nothing(self):
        self.assertEqual(recent.parse_recent(), [self.doc])

    def test_stats_record_which_source_was_used(self):
        _, st = recent.collect()
        self.assertEqual(st['structured'], 0)
        self.assertEqual(st['fallback'], 'GTK 最近使用')

    def test_primary_wins_when_it_works(self):
        with open(recent.CONF, 'w') as f:
            f.write('wps\\RecentFiles\\files\\1\\path=%s\n' % self.doc)
        _, st = recent.collect()
        self.assertEqual(st['fallback'], '')      # 主源有货就不碰兜底
        self.assertEqual(st['structured'], 1)

    def test_fallback_can_be_disabled(self):
        self.assertEqual(recent.parse_recent(allow_fallback=False), [])


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpsdiag_')
        self._orig = (recent.CONF, recent.XBEL_CANDIDATES, recent.CLOUD_DIRS)
        recent.XBEL_CANDIDATES = ()
        recent.CLOUD_DIRS = ()

    def tearDown(self):
        (recent.CONF, recent.XBEL_CANDIDATES,
         recent.CLOUD_DIRS) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_conf_says_so(self):
        recent.CONF = os.path.join(self.tmp, 'nope.conf')
        info = recent.diagnose()
        self.assertIn('没找到 WPS 的最近文档配置', info['hint'])
        self.assertEqual(info['conf_files'], [])

    def test_unparsable_conf_mentions_upgrade(self):
        recent.CONF = os.path.join(self.tmp, 'Office.conf')
        with open(recent.CONF, 'w') as f:
            f.write('wps\\NewFormat\\blob=QUJDREVGRw==\n')
        info = recent.diagnose()
        self.assertEqual(info['total'], 0)
        self.assertIn('WPS 升级', info['hint'])

    def test_records_but_missing_files(self):
        recent.CONF = os.path.join(self.tmp, 'Office.conf')
        with open(recent.CONF, 'w') as f:
            f.write('wps\\RecentFiles\\files\\1\\path=/gone/a.docx\n')
        info = recent.diagnose()
        self.assertEqual(info['total'], 1)
        self.assertEqual(info['existing'], 0)
        self.assertIn('文件都不在了', info['hint'])

    def test_healthy_conf_reports_count(self):
        recent.CONF = os.path.join(self.tmp, 'Office.conf')
        doc = os.path.join(self.tmp, 'ok.docx')
        open(doc, 'w').close()
        with open(recent.CONF, 'w') as f:
            f.write('wps\\RecentFiles\\files\\1\\path=%s\n' % doc)
        info = recent.diagnose()
        self.assertEqual(info['existing'], 1)
        self.assertIn('共 1 个文档', info['hint'])


if __name__ == '__main__':
    unittest.main()
