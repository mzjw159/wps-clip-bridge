# -*- coding: utf-8 -*-
"""自建 apt 源：目录结构、Packages 字段、Release 里的摘要是否自洽。

这些都是「apt 会替你算、但算不对就装不上」的东西：Release 里 Packages 的
哈希错一位，用户 apt update 就报 Hash Sum mismatch，而我们自己这边什么都
看不出来。所以生成完必须自己先按 apt 的规则校验一遍。
"""
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

TOOL = os.path.join(_ROOT, 'tools', 'build-apt-repo.py')
_HAS_DPKG = shutil.which('dpkg-deb') is not None


def make_deb(path, version, payload='x'):
    """造一个最小可用的 .deb（只要 dpkg-deb 认就行）。"""
    root = tempfile.mkdtemp(prefix='debroot_')
    try:
        os.makedirs(os.path.join(root, 'DEBIAN'))
        os.makedirs(os.path.join(root, 'usr', 'share', 'doc'))
        with open(os.path.join(root, 'usr', 'share', 'doc', 'a.txt'), 'w') as f:
            f.write(payload)
        with open(os.path.join(root, 'DEBIAN', 'control'), 'w') as f:
            f.write('Package: wps-clip-bridge\n'
                    'Version: %s\n'
                    'Architecture: all\n'
                    'Maintainer: t <t@example.com>\n'
                    'Section: utils\n'
                    'Priority: optional\n'
                    'Description: test package\n'
                    ' long description line\n'
                    ' .\n'
                    ' second paragraph\n' % version)
        subprocess.check_call(['dpkg-deb', '--build', '--root-owner-group',
                               root, path], stdout=subprocess.DEVNULL)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(_HAS_DPKG, '需要 dpkg-deb')
class AptRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aptrepo_')
        self.site = os.path.join(self.tmp, 'site')
        os.makedirs(self.site)
        self.deb1 = os.path.join(self.tmp, 'a_1.0.0_all.deb')
        self.deb2 = os.path.join(self.tmp, 'a_1.2.0_all.deb')
        make_deb(self.deb1, '1.0.0', 'old')
        make_deb(self.deb2, '1.2.0', 'new')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self, *debs):
        subprocess.check_call([sys.executable, TOOL, self.site] + list(debs),
                              stdout=subprocess.DEVNULL)

    def read(self, *parts):
        with open(os.path.join(self.site, 'apt', *parts), 'rb') as f:
            return f.read()

    def packages_text(self):
        return self.read('dists', 'stable', 'main', 'binary-all', 'Packages').decode('utf-8')

    def test_layout(self):
        self.run_tool(self.deb1)
        for p in (('dists', 'stable', 'Release'),
                  ('dists', 'stable', 'main', 'binary-all', 'Packages'),
                  ('dists', 'stable', 'main', 'binary-all', 'Packages.gz'),
                  ('pool', 'main', 'w', 'wps-clip-bridge',
                   'a_1.0.0_all.deb'),
                  ('index.html',)):
            self.assertTrue(os.path.isfile(os.path.join(self.site, 'apt', *p)),
                            '缺 %s' % '/'.join(p))

    def test_packages_fields(self):
        self.run_tool(self.deb1)
        text = self.packages_text()
        for field in ('Package: wps-clip-bridge', 'Version: 1.0.0',
                      'Architecture: all', 'Filename: pool/main/w/',
                      'Size: ', 'MD5sum: ', 'SHA256: '):
            self.assertIn(field, text)
        # 描述里的续行不能被压平（apt 靠缩进区分字段与段落）
        self.assertIn(' long description line', text)

    def test_filename_and_hashes_match_file(self):
        self.run_tool(self.deb1)
        text = self.packages_text()
        rel = [l.split(': ', 1)[1] for l in text.splitlines()
               if l.startswith('Filename: ')][0]
        deb = os.path.join(self.site, 'apt', rel)
        self.assertTrue(os.path.isfile(deb), 'Filename 指向的文件不存在')
        with open(deb, 'rb') as f:
            data = f.read()
        size = [int(l.split(': ', 1)[1]) for l in text.splitlines()
                if l.startswith('Size: ')][0]
        sha = [l.split(': ', 1)[1] for l in text.splitlines()
               if l.startswith('SHA256: ')][0]
        self.assertEqual(size, len(data))
        self.assertEqual(sha, hashlib.sha256(data).hexdigest())

    def test_release_hashes_match_packages(self):
        self.run_tool(self.deb1)
        rel = self.read('dists', 'stable', 'Release').decode('utf-8')
        pkgs = self.read('dists', 'stable', 'main', 'binary-all', 'Packages')
        gz = self.read('dists', 'stable', 'main', 'binary-all', 'Packages.gz')

        def section(name):
            """取 Release 里某个摘要段的所有行（MD5Sum / SHA256）。"""
            out, inside = [], False
            for line in rel.splitlines():
                if line.rstrip(':') in ('MD5Sum', 'SHA256'):
                    inside = line.startswith(name + ':')
                    continue
                if inside and line.startswith(' '):
                    out.append(line.split())
            return out

        def entry(digest_block, name):
            for parts in digest_block:
                if len(parts) == 3 and parts[2].endswith(name):
                    return parts[0], int(parts[1])
            return None, None

        sha_block = section('SHA256')
        md5_block = section('MD5Sum')
        sha, size = entry(sha_block, 'Packages')
        self.assertEqual(sha, hashlib.sha256(pkgs).hexdigest())
        self.assertEqual(size, len(pkgs))
        md5, size = entry(md5_block, 'Packages')
        self.assertEqual(md5, hashlib.md5(pkgs).hexdigest())
        self.assertEqual(size, len(pkgs))
        sha, size = entry(sha_block, 'Packages.gz')
        self.assertEqual(sha, hashlib.sha256(gz).hexdigest())
        self.assertEqual(size, len(gz))
        # gzip 里必须真的是那份 Packages
        self.assertEqual(gzip.decompress(gz), pkgs)
        # 路径相对于 dists/stable/
        self.assertIn(' main/binary-all/Packages', rel)

    def test_two_versions_both_kept(self):
        self.run_tool(self.deb1)
        self.run_tool(self.deb2)
        text = self.packages_text()
        self.assertIn('Version: 1.0.0', text)
        self.assertIn('Version: 1.2.0', text)
        # 新版排前面：Packages 的顺序会影响 apt 的默认候选
        self.assertLess(text.index('Version: 1.2.0'), text.index('Version: 1.0.0'))

    def test_repack_is_byte_stable(self):
        # 内容没变时重复生成，Packages.gz 必须是同样的字节，
        # 否则每次发版都会在仓库里留下一个「什么都没改」的提交
        self.run_tool(self.deb1)
        first = self.read('dists', 'stable', 'main', 'binary-all', 'Packages.gz')
        self.run_tool(self.deb1)
        second = self.read('dists', 'stable', 'main', 'binary-all', 'Packages.gz')
        self.assertEqual(first, second)

    def test_index_not_overwritten(self):
        self.run_tool(self.deb1)
        index = os.path.join(self.site, 'apt', 'index.html')
        with open(index, 'w') as f:
            f.write('手改过')
        self.run_tool(self.deb2)
        with open(index) as f:
            self.assertEqual(f.read(), '手改过')


if __name__ == '__main__':
    unittest.main()
