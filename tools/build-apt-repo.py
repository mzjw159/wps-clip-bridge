#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .deb 变成一套可以直接放进任何静态站点（GitHub Pages 就行）的 apt 源。

用法::

    tools/build-apt-repo.py <站点根目录> <a.deb> [b.deb ...]

生成的目录结构：

    <根>/apt/index.html                         说明页
    <根>/apt/pool/main/w/wps-clip-bridge/*.deb  所有历史版本都留着（可降级）
    <根>/apt/dists/stable/Release
    <根>/apt/dists/stable/main/binary-all/Packages{,.gz}

为什么不做 GPG 签名：签名得管一把长期密钥，密钥泄了比不签更麻烦；而
`[trusted=yes]` 写进 sources.list 的用户其实已经明说「我信任这一个源」。
所以文档里直接给带 `[trusted=yes]` 的那一行，别装作有签名。

每次把新 deb 丢进来重跑即可：旧的留在 pool 里，Packages 全量重扫。
"""
from __future__ import print_function

import gzip
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time

SUITE = 'stable'
COMPONENT = 'main'
ARCH = 'all'


def read_control(deb):
    """dpkg-deb -f 的输出就是一段控制文件，逐行保留（续行以空格开头）。"""
    try:
        out = subprocess.check_output(['dpkg-deb', '-f', deb],
                                      stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as ex:
        raise SystemExit('读不出 %s 的 control：%s'
                         % (deb, ex.output.decode('utf-8', 'replace')))
    return out.decode('utf-8', 'replace')


def field(text, name):
    prefix = name.lower() + ':'
    for line in text.splitlines():
        if line.lower().startswith(prefix):
            return line[len(prefix):].strip()
    return ''


def hashes(path):
    h256 = hashlib.sha256()
    hmd5 = hashlib.md5()
    size = 0
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            h256.update(chunk)
            hmd5.update(chunk)
    return size, hmd5.hexdigest(), h256.hexdigest()


def pool_relpath(pkg, debname):
    # Debian 的惯例：pool/<区>/<前缀>/<包名>/，区用 main；前缀取包名首字母，
    # libfoo 取 libf（不是 l），这样一个库的包会聚在一起。
    # 注意用控制文件里的 Package 名，不是 deb 文件名 —— 两者不一致时（改名、
    # 架构后缀）按文件名归档会让同一个包散落在两个目录里。
    prefix = pkg[:4] if pkg.startswith('lib') else pkg[:1]
    return os.path.join('pool', COMPONENT, prefix, pkg, debname)


def install_deb(apt_root, deb):
    ctrl = read_control(deb)
    pkg = field(ctrl, 'Package')
    if not pkg:
        raise SystemExit('%s 里没有 Package 字段' % deb)
    rel = pool_relpath(pkg, os.path.basename(deb))
    dst = os.path.join(apt_root, rel)
    if not os.path.isdir(os.path.dirname(dst)):
        os.makedirs(os.path.dirname(dst))
    shutil.copy2(deb, dst)
    return rel


def walk_debs(apt_root):
    """pool 里所有 deb，按包名 + 版本排序（新版在前）。"""
    out = []
    pool = os.path.join(apt_root, 'pool')
    for root, _dirs, files in os.walk(pool):
        for f in files:
            if f.endswith('.deb'):
                out.append(os.path.join(root, f))
    out.sort()
    return out


def version_key(ver):
    """粗排版本号：数字段按数值比，其余按字符串。够用来决定 Packages 里的顺序。"""
    parts = []
    for chunk in ver.replace('-', '.').replace('~', '.').split('.'):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))
    return parts


def build_packages(apt_root):
    entries = []
    for path in walk_debs(apt_root):
        ctrl = read_control(path)
        rel = os.path.relpath(path, apt_root)
        size, md5, sha256 = hashes(path)
        body = ctrl.rstrip('\n')
        # 这三个字段由仓库决定，不能沿用 deb 里可能存在的旧值
        lines = [l for l in body.splitlines()
                 if not l.lower().startswith(('filename:', 'size:', 'md5sum:',
                                              'sha1:', 'sha256:'))]
        lines.append('Filename: %s' % rel.replace(os.sep, '/'))
        lines.append('Size: %d' % size)
        lines.append('MD5sum: %s' % md5)
        lines.append('SHA256: %s' % sha256)
        entries.append((field(ctrl, 'Package'), version_key(field(ctrl, 'Version')),
                        '\n'.join(lines) + '\n'))

    entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
    return '\n'.join(e[2] for e in entries)


def build_release(apt_root, packages_bytes, packages_gz_bytes):
    def digest(data):
        return hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()

    rel = '%s/binary-%s/' % (COMPONENT, ARCH)
    m1, s1 = digest(packages_bytes)
    m2, s2 = digest(packages_gz_bytes)
    now = time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime())
    out = [
        'Origin: wps-clip-bridge',
        'Label: wps-clip-bridge',
        'Suite: %s' % SUITE,
        'Codename: %s' % SUITE,
        'Architectures: %s' % ARCH,
        'Components: %s' % COMPONENT,
        'Description: WPS 最近文档 - 自建 apt 源（未签名，需 [trusted=yes]）',
        'Date: %s' % now,
        'MD5Sum:',
        ' %s %d %sPackages' % (m1, len(packages_bytes), rel),
        ' %s %d %sPackages.gz' % (m2, len(packages_gz_bytes), rel),
        'SHA256:',
        ' %s %d %sPackages' % (s1, len(packages_bytes), rel),
        ' %s %d %sPackages.gz' % (s2, len(packages_gz_bytes), rel),
    ]
    return '\n'.join(out) + '\n'


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>WPS 最近文档 · apt 源</title>
<style>
 body {{ font: 15px/1.7 -apple-system, "Noto Sans CJK SC", sans-serif;
        max-width: 760px; margin: 48px auto; padding: 0 20px; color: #222; }}
 h1 {{ font-size: 24px; }} code {{ background: #f4f4f5; padding: 2px 6px;
 border-radius: 4px; }} pre {{ background: #f4f4f5; padding: 14px 16px;
 border-radius: 8px; overflow-x: auto; }} pre code {{ background: none;
 padding: 0; }} a {{ color: #125a9e; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background: #16181c; color: #e6e6e6; }}
   code, pre {{ background: #23262b; }} a {{ color: #7fb2ff; }}
 }}
</style>
<h1>WPS 最近文档 · apt 源</h1>
<p>加一次，以后 <code>sudo apt upgrade</code> 就能升级，不用每次来下载安装包。</p>
<pre><code>echo "deb [trusted=yes] {base} {suite} main" \\
  | sudo tee /etc/apt/sources.list.d/wps-clip-bridge.list
sudo apt update
sudo apt install wps-clip-bridge</code></pre>
<p>这个源<strong>没有 GPG 签名</strong>，所以那一行必须带 <code>[trusted=yes]</code> ——
意思是你明确信任这一个源。介意的话请走
<a href="https://github.com/mzjw159/wps-clip-bridge/releases">Release 页面</a>手动下载。</p>
<p>项目主页：<a href="https://github.com/mzjw159/wps-clip-bridge">github.com/mzjw159/wps-clip-bridge</a></p>
"""


def write_index(apt_root, base_url):
    path = os.path.join(apt_root, 'index.html')
    if os.path.exists(path):
        return                      # 已经生成过就别覆盖（可能手改过）
    io.open(path, 'w', encoding='utf-8').write(
        INDEX_HTML.format(base=base_url.rstrip('/') + '/apt', suite=SUITE))


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    site_root = os.path.abspath(argv[0])
    debs = argv[1:]
    apt_root = os.path.join(site_root, 'apt')
    for d in ('pool', 'dists'):
        p = os.path.join(apt_root, d)
        if not os.path.isdir(p):
            os.makedirs(p)

    added = []
    for deb in debs:
        if not os.path.isfile(deb):
            print('跳过不存在的 %s' % deb, file=sys.stderr)
            continue
        added.append(install_deb(apt_root, deb))

    packages = build_packages(apt_root).encode('utf-8')
    bin_dir = os.path.join(apt_root, 'dists', SUITE, COMPONENT,
                           'binary-%s' % ARCH)
    if not os.path.isdir(bin_dir):
        os.makedirs(bin_dir)

    with open(os.path.join(bin_dir, 'Packages'), 'wb') as f:
        f.write(packages)
    # mtime=0：内容相同就产生相同的字节，仓库不会有「什么都没改却 diff 一片」的提交
    gz_path = os.path.join(bin_dir, 'Packages.gz')
    with open(gz_path, 'wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as gz:
            gz.write(packages)
    with open(gz_path, 'rb') as f:
        gz_bytes = f.read()

    release = build_release(apt_root, packages, gz_bytes)
    with io.open(os.path.join(apt_root, 'dists', SUITE, 'Release'),
                 'w', encoding='utf-8') as f:
        f.write(release)

    base = os.environ.get('APT_BASE_URL', 'https://mzjw159.github.io/wps-clip-bridge')
    write_index(apt_root, base)

    print('加入 %d 个包，pool 里现有 %d 个：' % (len(added), len(walk_debs(apt_root))))
    for rel in added:
        print('  + %s' % rel)
    print('Packages: %d 字节，%d 个包'
          % (len(packages), packages.count(b'\n\nPackage:') + 1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
