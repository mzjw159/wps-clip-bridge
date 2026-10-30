# -*- coding: utf-8 -*-
"""``wps-copy --list`` / ``--pin`` 的输出。

命令行是给脚本和「不想开窗口」的人用的，输出格式一变就可能弄坏别人的管道，
所以字段、序号、置顶标记都要钉住。
"""
import contextlib
import io
import json
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

from wpsclip import cli, core, recent  # noqa: E402

_ENV_KEYS = ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR')


class _Fixture(unittest.TestCase):
    """临时家目录 + 临时 Office.conf，两三个真文件。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpscli_')
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.tmp, 'config')
        os.environ['XDG_STATE_HOME'] = os.path.join(self.tmp, 'state')
        os.environ['XDG_RUNTIME_DIR'] = os.path.join(self.tmp, 'run')
        for d in ('config', 'state', 'run'):
            os.makedirs(os.path.join(self.tmp, d), exist_ok=True)

        self.conf = os.path.join(self.tmp, 'Office.conf')
        self._orig_conf = recent.CONF
        self._orig_xbel = recent.XBEL_CANDIDATES
        self._orig_cloud = recent.CLOUD_DIRS
        recent.CONF = self.conf
        recent.XBEL_CANDIDATES = ()      # 别让真实机器上的兜底数据源混进来
        recent.CLOUD_DIRS = ()
        core._config = None

    def tearDown(self):
        recent.CONF = self._orig_conf
        recent.XBEL_CANDIDATES = self._orig_xbel
        recent.CLOUD_DIRS = self._orig_cloud
        core._config = None
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, age=10):
        p = os.path.join(self.tmp, name)
        with open(p, 'w') as f:
            f.write('x' * 32)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def write_conf(self, paths):
        with open(self.conf, 'w', encoding='utf-8') as f:
            for i, p in enumerate(paths, 1):
                f.write('wps\\RecentFiles\\files\\%d\\path=%s\n' % (i, p))

    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()


class ListTest(_Fixture):
    def test_columns_and_pin_mark(self):
        old = self.make('年度报告.docx', 100)
        new = self.make('预算.xlsx', 10)
        self.write_conf([old, new])
        core.set_pinned([new])

        code, out = self.run_cli(['--list'])
        self.assertEqual(code, 0)
        # 序号按修改时间：新的在前；置顶只是加个 ★，不改变序号
        self.assertIn('1. ★ 表格', out)
        self.assertIn('2.   文档', out)
        self.assertIn('预算.xlsx', out)
        self.assertIn('共 2 个（1 个置顶）', out)

    def test_numbers_match_pick(self):
        a = self.make('a.docx', 30)
        b = self.make('b.docx', 20)
        c = self.make('c.docx', 10)
        self.write_conf([a, b, c])
        code, out = self.run_cli(['--list'])
        self.assertEqual(code, 0)
        lines = [ln for ln in out.splitlines() if ln.strip().startswith(('1.', '2.', '3.'))]
        # 第 2 行的路径必须就是 wps-copy 2 复制的东西
        self.assertTrue(lines[1].endswith(b), lines[1])
        self.assertEqual(cli.pick(['2']), [b])

    def test_keyword_filter(self):
        self.make('周报.docx', 10)
        self.make('预算.xlsx', 20)
        self.write_conf([os.path.join(self.tmp, '周报.docx'),
                         os.path.join(self.tmp, '预算.xlsx')])
        code, out = self.run_cli(['--list', '周报'])
        self.assertEqual(code, 0)
        self.assertIn('周报.docx', out)
        self.assertNotIn('预算.xlsx', out)

    def test_no_match_exit_code(self):
        self.make('周报.docx', 10)
        self.write_conf([os.path.join(self.tmp, '周报.docx')])
        code, out = self.run_cli(['--list', '没有这个'])
        self.assertEqual(code, 1)
        self.assertIn('没有符合', out)

    def test_empty_source_explains(self):
        # 一条配置都没有：给出人话诊断，而不是一句「空的」
        code, out = self.run_cli(['--list'])
        self.assertEqual(code, 1)
        self.assertIn('--doctor', out)

    def test_pinned_only(self):
        self.make('a.docx', 10)
        b = self.make('b.pdf', 5)
        self.write_conf([os.path.join(self.tmp, 'a.docx'), b])
        core.set_pinned([b])
        code, out = self.run_cli(['--list', '--pinned'])
        self.assertEqual(code, 0)
        self.assertIn('b.pdf', out)
        self.assertNotIn('a.docx', out)


class JsonTest(_Fixture):
    def test_fields(self):
        p = self.make('合同.docx', 10)
        self.write_conf([p])
        core.set_pinned([p])
        code, out = self.run_cli(['--list', '--json'])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        row = data[0]
        self.assertEqual(row['path'], p)
        self.assertEqual(row['name'], '合同.docx')
        self.assertEqual(row['dir'], self.tmp)
        self.assertEqual(row['kind'], '文档')
        self.assertEqual(row['source'], '本地')
        self.assertTrue(row['pinned'])
        self.assertGreater(row['mtime'], 0)
        self.assertIn('B', row['size'])
        self.assertEqual(row['size_bytes'], 32)   # 夹具写的就是 32 字节

    def test_cloud_source_reported(self):
        p = self.make('云合同.docx', 10)
        path = os.path.join(self.tmp, 'WPS Cloud Files', '云合同.docx')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(p, path)
        self.write_conf([path])
        code, out = self.run_cli(['--list', '-j'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]['source'], '云端')


class PinTest(_Fixture):
    def test_pin_puts_newest_first(self):
        a = self.make('a.docx', 10)
        b = self.make('b.docx', 5)
        code, out = self.run_cli(['--pin', a, b])
        self.assertEqual(code, 0)
        self.assertEqual(core.pinned_paths(), [a, b])   # 后来的插到最前
        self.assertIn('已置顶', out)

    def test_pin_is_idempotent(self):
        a = self.make('a.docx', 10)
        self.run_cli(['--pin', a])
        code, out = self.run_cli(['--pin', a])
        self.assertEqual(code, 0)
        self.assertIn('无需改动', out)
        self.assertEqual(core.pinned_paths(), [a])

    def test_unpin(self):
        a = self.make('a.docx', 10)
        self.run_cli(['--pin', a])
        code, _out = self.run_cli(['--unpin', a])
        self.assertEqual(code, 0)
        self.assertEqual(core.pinned_paths(), [])

    def test_missing_file_rejected(self):
        gone = os.path.join(self.tmp, 'gone.docx')
        code, out = self.run_cli(['--pin', gone])
        self.assertEqual(code, 1)
        self.assertIn('文件不存在', out)
        self.assertEqual(core.pinned_paths(), [])

    def test_relative_path_made_absolute(self):
        a = self.make('a.docx', 10)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            code, _out = self.run_cli(['--pin', 'a.docx'])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)
        self.assertEqual(core.pinned_paths(), [a])

    def test_bare_pin_lists_current(self):
        a = self.make('a.docx', 10)
        self.run_cli(['--pin', a])
        code, out = self.run_cli(['--pin'])
        self.assertEqual(code, 0)
        self.assertIn('当前置顶 1 个', out)
        self.assertIn(a, out)


class SortTest(_Fixture):
    """``--list --sort``：字段名、方向、以及和置顶的配合。"""

    def make_sized(self, name, size, age=10):
        p = os.path.join(self.tmp, name)
        with open(p, 'w') as f:
            f.write('x' * size)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def listed(self, out):
        return [ln for ln in out.splitlines() if ln[:1].isdigit()]

    def test_sort_by_size(self):
        small = self.make_sized('小.docx', 10, 30)
        big = self.make_sized('大.docx', 4000, 20)
        mid = self.make_sized('中.docx', 500, 10)
        self.write_conf([small, big, mid])

        code, out = self.run_cli(['--list', '--sort', 'size'])
        self.assertEqual(code, 0)
        lines = self.listed(out)
        self.assertTrue(lines[0].endswith(small), lines[0])
        self.assertTrue(lines[-1].endswith(big), lines[-1])

    def test_sort_by_size_reverse(self):
        small = self.make_sized('小.docx', 10, 30)
        big = self.make_sized('大.docx', 4000, 20)
        self.write_conf([small, big])
        _code, out = self.run_cli(['--list', '--sort', 'size', '--reverse'])
        self.assertTrue(self.listed(out)[0].endswith(big))

    def test_sort_by_name(self):
        b = self.make('b.docx', 10)
        a = self.make('a.docx', 20)
        self.write_conf([b, a])
        _code, out = self.run_cli(['--list', '-s', 'name'])
        lines = self.listed(out)
        self.assertTrue(lines[0].endswith(a))
        self.assertTrue(lines[1].endswith(b))

    def test_pin_mark_survives_sorting(self):
        # 排序只改顺序，★ 与序号含义都不能变
        b = self.make('b.docx', 10)
        a = self.make('a.docx', 20)
        self.write_conf([b, a])
        core.set_pinned([b])
        _code, out = self.run_cli(['--list', '--sort', 'name'])
        self.assertIn('1.   文档', out)
        self.assertIn('2. ★ 文档', out)

    def test_unknown_sort_field(self):
        self.write_conf([self.make('a.docx')])
        code, out = self.run_cli(['--list', '--sort', 'nope'])
        self.assertEqual(code, 2)
        self.assertIn('不认识的排序字段', out)

    def test_sort_without_value(self):
        code, out = self.run_cli(['--list', '--sort'])
        self.assertEqual(code, 2)
        self.assertIn('后面要跟字段', out)


class SearchTermsTest(_Fixture):
    """多个关键词：空格分开，每个都要命中；命令行与图形界面同一套语义。"""

    def setUp(self):
        super(SearchTermsTest, self).setUp()
        self.report = self.make('季度报告.docx', 10)
        self.budget = self.make('季度预算.xlsx', 20)
        self.write_conf([self.report, self.budget])

    def test_list_all_words_must_match(self):
        code, out = self.run_cli(['--list', '季度', '报告'])
        self.assertEqual(code, 0)
        self.assertIn('季度报告.docx', out)
        self.assertNotIn('季度预算.xlsx', out)

    def test_list_word_inside_one_argument(self):
        # 带引号传进来的 "季度 报告" 也要能分词
        _code, out = self.run_cli(['--list', '季度 报告'])
        self.assertIn('季度报告.docx', out)
        self.assertNotIn('季度预算.xlsx', out)

    def test_pick_joins_words(self):
        self.assertEqual(cli.pick(['季度', '报告']), [self.report])

    def test_pick_no_match(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertIsNone(cli.pick(['季度', '没有的词']))
        self.assertIn('没有匹配', buf.getvalue())


class UsageTest(unittest.TestCase):
    def test_unknown_list_option(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_list(['--nope'])
        self.assertEqual(code, 2)
        self.assertIn('未知选项', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
