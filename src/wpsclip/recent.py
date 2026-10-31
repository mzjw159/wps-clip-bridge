#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""读取 WPS 的「最近打开文档」列表 —— 多数据源，坏一个不至于全空。

数据主源是 ``~/.config/Kingsoft/Office.conf``，形如::

    pdf\\RecentFiles\\files\\1\\path=/home/u/\\x684c\\x9762/\\x901a\\x77e5.pdf

转义规则（实测）：``\\x`` 后跟 **2~4 位**十六进制 Unicode 码点，遇到非十六进制
字符即停。不是定长 4 位 —— ASCII 字符（如文件名里的 "2026"）会写成
``\\x32\\x30\\x32\\x36``，若按定长 4 位解析会把 ``\\x32\\x30`` 吃成 0x3230，
整条路径就废了、文件被判为不存在而被过滤掉。

**为什么要有备用数据源**：Office.conf 是 WPS 内部私有格式，不是公开接口。
它哪天把 ``RecentFiles`` 改个名、把转义换个方式，只认键名的解析就会静默
返回空列表（用户只看到「列表空的」，什么提示都没有）。所以这里分三层：

1. **结构化解析**（认键名，能拿到序号）；
2. **通用扫描**（同一个文件里，凡是「值像被转义的绝对路径、且指向真实文档」
   的键值对全部收进来）—— 键名怎么改都不影响这一层；
3. **兜底数据源**（主源一条都读不出来时才启用）：GTK 的
   ``recently-used.xbel``、WPS 云文档本地目录里最近改过的文档。

**顺序从哪来**：``files\\N\\path`` 里的 N 只是「该类型内部的位次」—— wps/et/
pdf 各有一套、都从 1 开始，跨类型没法比。真正的顺序在
``wps\\RecentFiles\\Sequence\\<编码路径>=<Unix 秒>``：那是**打开时间**，跨类型
可比、也能拿来分「今天/昨天」。早期版本拿文件 mtime 当顺序，于是「打开一份
老文档但不改内容」不会挪到最前面 —— 和 WPS 自己看到的顺序对不上。

任何一层有收获就用，全都没有时由 :func:`diagnose` 给出**人话解释**，
而不是让界面显示一句空的。
"""
import os
import re
import urllib.parse

# 主数据源（测试里会替换成临时文件）
CONF = os.path.expanduser('~/.config/Kingsoft/Office.conf')
_DEFAULT_CONF = CONF

# 别名：个别版本的 WPS / 大小写不一致的部署
CONF_ALIASES = (
    '~/.config/kingsoft/Office.conf',
    '~/.config/Kingsoft/office.conf',
)

# 兜底数据源
XBEL_CANDIDATES = (
    '~/.local/share/recently-used.xbel',
    '~/.recently-used.xbel',
)
CLOUD_DIRS = (
    '~/文档/WPS Cloud Files',
    '~/文档/WPS 云文档',
    '~/Documents/WPS Cloud Files',
    '~/WPS Cloud Files',
)

# 能当「文档」处理的扩展名（通用扫描只认这些，避免把日志、配置当文档）。
# 故意不含 .txt：WPS 不会打开纯文本，而在配置里出现 .txt 的多半是日志路径。
DOC_EXTS = (
    '.doc', '.docx', '.wps', '.wpt', '.rtf',
    '.xls', '.xlsx', '.et', '.ett', '.csv',
    '.ppt', '.pptx', '.dps', '.dpt',
    '.pdf', '.ofd',
)

_HEX = '0123456789abcdefABCDEF'

# 本地文档的最近打开记录
_PATH_RE = re.compile(
    r'^(?:wps|et|wpp|pdf|ofd)'
    r'\\RecentFiles\\files\\(\d+)\\(\w+)=(.*)$', re.M | re.I)

# 云文档 / 办公空间打开记录（打开过的云文件通常也在本地留了缓存副本）
_SPACE_RE = re.compile(
    r'^plugins\\officespace\\[^\n]*?\\openfilelist\\(\d+)\\(path|localpath)=(.*)$',
    re.M | re.I)

# 打开时间表：``wps\RecentFiles\Sequence\<百分号编码的路径>=<Unix 秒>``
#
# 这是「最近文档」真正的排序依据。值限定 9~13 位数字（2001 年之后的秒级
# 时间戳），免得把路径里恰好含「=数字」的情况误当成时间。路径部分用贪婪
# 匹配：文件名里带 = 时，靠「值必须是纯数字」来定界才是安全的。
_SEQ_RE = re.compile(
    r'^(?:wps|et|wpp|pdf|ofd)\\RecentFiles\\Sequence\\(.+)=(\d{9,13})$',
    re.M | re.I)

# Sequence 的键是 WPS 自己的百分号编码：%UXXXX 表示一个 UTF-16 码点
# （中文、空格都这么写），其余按标准 percent-encoding。
_PCT_U = re.compile(r'%[Uu]([0-9a-fA-F]{4})')

# 通用扫描：任何 key=value
_ASSIGN_RE = re.compile(r'^([^=\n]+)=(.*)$', re.M)

# 判断「路径是不是 WPS 自己的内部文件」（模板、缓存、字体等）
_INTERNAL = ('/Kingsoft/office6/', '/.local/share/Kingsoft/')


def unesc(s):
    """把 WPS 的 ``\\xHH[HH]`` 转义还原成真实字符串。"""
    out = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == '\\' and i + 1 < n and s[i + 1] == 'x':
            j = i + 2
            while j < n and j - (i + 2) < 4 and s[j] in _HEX:
                j += 1
            if j > i + 2:
                try:
                    out.append(chr(int(s[i + 2:j], 16)))
                    i = j
                    continue
                except ValueError:
                    pass
            out.append(s[i])
            i += 1
            continue
        if s[i] == '\\' and i + 1 < n and s[i + 1] == '\\':
            out.append('\\')
            i += 2
            continue
        out.append(s[i])
        i += 1
    return ''.join(out)


def unesc_pct(raw):
    """还原 Sequence 的键：``%U6587`` 是一个 UTF-16 码点，``%20`` 是空格。

    键写成 ``home\\shuai\\%U6587%U6863\\x.docx`` —— 反斜杠当分隔符、没有前导
    斜杠，所以统一换成 ``/`` 并补上开头的 ``/``，否则 ``os.path.isfile`` 全
    落空，整层顺序信息就白读了。
    """
    if not raw:
        return ''
    s = _PCT_U.sub(lambda m: chr(int(m.group(1), 16)), raw)
    s = urllib.parse.unquote(s, errors='replace')
    s = s.replace('\\', '/')
    if s.startswith('file://'):
        s = s[7:]
    if s and not s.startswith('/'):
        s = '/' + s
    return s


def parse_sequences(text):
    """``{路径: 打开时间戳}``，同一文件被多个产品段打开过时取最新的那次。

    WPS 自己也是这么排的（wps/et/pdf 打开同一份文档，各写一条记录），取最大
    值才不会因为「先拿 PDF 打开、后拿 WPS 打开」把顺序排回去。
    """
    out = {}
    for m in _SEQ_RE.finditer(text or ''):
        path = unesc_pct(m.group(1))
        if not path or not _is_doc(path):
            continue
        try:
            ts = int(m.group(2))
        except ValueError:
            continue
        if ts > out.get(path, 0):
            out[path] = ts
    return out


def open_times():
    """读打开时间表；没有配置文件时返回空字典。"""
    data, _used = read_conf_text()
    return parse_sequences(data) if data else {}


# ---------------------------------------------------------------- 主数据源

def conf_paths():
    """要尝试的配置文件路径（存在的、去重、按优先级）。

    ``CONF`` 被外部替换过（测试、或将来允许 --config 指定）时就只用它，
    不再顺带读别名 —— 否则「我在测试里指了临时文件」会变成读了真实配置。
    """
    out = []
    if os.path.isfile(CONF):
        out.append(CONF)
    if CONF == _DEFAULT_CONF:
        for c in CONF_ALIASES:
            p = os.path.expanduser(c)
            if p not in out and os.path.isfile(p):
                out.append(p)
    return out


def read_conf_text():
    """把主数据源读成一段文本；都没有返回 (None, '')。"""
    for p in conf_paths():
        try:
            with open(p, 'rb') as f:
                return f.read().decode('utf-8', 'replace'), p
        except OSError:
            continue
    return None, ''


def _collect(data):
    """结构化解析：返回 {真实路径: 记录序号}，同路径只保留首次出现。"""
    found = {}
    groups = (_PATH_RE, _SPACE_RE)
    for pat in groups:
        for m in pat.finditer(data):
            raw = m.group(m.lastindex).strip()
            if not raw or '\n' in raw:
                continue
            path = unesc(raw)
            if not path.startswith('/'):
                continue
            if path not in found:
                found[path] = m.group(2) if pat is _PATH_RE else '0'
    return found


def _is_internal(path):
    return any(s in path for s in _INTERNAL)


def _is_doc(path):
    return os.path.splitext(path)[1].lower() in DOC_EXTS


def scan_assignments(data):
    """通用扫描：不看键名，只看值。

    WPS 若把 ``RecentFiles`` 改名、层级改结构，结构化解析会条条落空；但只要
    它还在用「键=被转义的绝对路径」这种写法，这一层就能把文档捞回来。
    只收同时满足三个条件的值：看起来是绝对路径、扩展名属于文档、不是 WPS
    自己的模板/缓存。
    """
    out = []
    for m in _ASSIGN_RE.finditer(data):
        raw = m.group(2).strip()
        if not raw or '\n' in raw or len(raw) > 4096:
            continue
        if '\\x' in raw:
            path = unesc(raw)
        elif raw.startswith('file://'):
            path = urllib.parse.unquote(raw[7:])
        elif raw.startswith('/'):
            path = raw
        else:
            continue
        if not path.startswith('/') or not _is_doc(path) or _is_internal(path):
            continue
        if path not in out:
            out.append(path)
    return out


# ---------------------------------------------------------------- 兜底数据源

_BOOKMARK_RE = re.compile(r'<bookmark\b([^>]*)>', re.I | re.S)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_xbel(text):
    """解析 GTK 的最近使用记录，只留本地文档（按记录顺序，最新的在前）。"""
    out = []
    for m in _BOOKMARK_RE.finditer(text or ''):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        uri = attrs.get('href', '')
        if not uri.startswith('file://'):
            continue
        path = urllib.parse.unquote(uri[7:])
        if not path.startswith('/') or not _is_doc(path) or _is_internal(path):
            continue
        if path not in out:
            out.append(path)
    return out


def read_xbel():
    for c in XBEL_CANDIDATES:
        p = os.path.expanduser(c)
        try:
            with open(p, 'rb') as f:
                return parse_xbel(f.read().decode('utf-8', 'replace'))
        except OSError:
            continue
    return []


def scan_cloud_dirs(max_files=200, max_entries=6000):
    """扫云文档本地目录，按修改时间取最近的文档。

    WPS 云文档在本地 `~/文档/WPS Cloud Files/<账号>/…` 有副本，文件名就是
    真名。主数据源失效时，这是唯一还能反映「最近在改什么」的地方。
    """
    rows = []
    seen = 0
    for base in CLOUD_DIRS:
        root = os.path.expanduser(base)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 4:
                dirnames[:] = []
            for name in filenames:
                seen += 1
                if seen > max_entries:
                    return _rank(rows, max_files)
                if not _is_doc(name):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    rows.append((os.path.getmtime(p), p))
                except OSError:
                    continue
    return _rank(rows, max_files)


def _rank(rows, limit):
    rows.sort(reverse=True)
    out, seen = [], set()
    for _, p in rows:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 汇总

def collect_meta(only_exist=True, allow_fallback=True):
    """按层收集候选路径，返回 ``([(路径, 打开时间或 0)], 统计)``。

    打开时间来自 WPS 的 Sequence 表，是「最近文档」的顺序依据；没有记录的
    （兜底数据源、老版本 WPS 的配置）记 0，排序时退回文件修改时间。

    统计里带每一层的命中数，:func:`diagnose` 和 ``wps-copy --doctor`` 都靠它。
    """
    stats = {'conf': '', 'structured': 0, 'scanned': 0,
             'conf_files': [], 'fallback': '', 'fallback_hits': 0,
             'opened': 0}
    paths = {}

    data, used = read_conf_text()
    stats['conf'] = used
    for p in conf_paths():
        try:
            stats['conf_files'].append((p, os.path.getsize(p)))
        except OSError:
            pass

    opened = parse_sequences(data) if data else {}

    if data:
        structured = _collect(data)
        for p in structured:
            paths.setdefault(p, '结构化')
        stats['structured'] = len(structured)
        scanned = scan_assignments(data)
        for p in scanned:
            paths.setdefault(p, '通用扫描')
        stats['scanned'] = len(scanned)

    if not paths and allow_fallback:
        for label, getter in (('GTK 最近使用', read_xbel),
                              ('云文档目录', scan_cloud_dirs)):
            hits = getter()
            if hits:
                stats['fallback'] = label
                stats['fallback_hits'] = len(hits)
                for p in hits:
                    paths.setdefault(p, label)
                break

    # 排序键：(有没有打开时间, 时间, mtime, 路径)。
    #
    # 第一个分量是档位：有 WPS 打开记录的整批排在前面。不这么分的话，
    # 「一分钟前刚被别的软件改过、但 WPS 没打开过」的文件会压过「半分钟前
    # 刚打开过」的文件 —— 用户明明看到 WPS 里它在第一，这边却是第二。
    # 全都没有记录时（兜底数据源）档位相同，退化成按 mtime 排，行为不变。
    rows = []
    for p in paths:
        if only_exist and not os.path.isfile(p):
            continue
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0
        ts = opened.get(p, 0)
        rows.append((1 if ts else 0, ts or mt, mt, p))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]), reverse=True)
    stats['total'] = len(paths)
    stats['missing'] = len(paths) - len(rows)
    stats['opened'] = sum(1 for row in rows if row[0])
    return [(p, opened.get(p, 0)) for _tier, _when, _mt, p in rows], stats


def collect(only_exist=True, allow_fallback=True):
    """只返回路径列表（顺序同上）。给命令行、托盘这些不关心打开时间的地方用。"""
    rows, stats = collect_meta(only_exist, allow_fallback)
    return [p for p, _ts in rows], stats


def parse_recent(limit=80, only_exist=True, allow_fallback=True):
    """按「最近打开」倒序（无记录时按修改时间），返回真实路径列表。"""
    paths, _ = collect(only_exist=only_exist, allow_fallback=allow_fallback)
    return paths[:limit]


def _fmt_size(n):
    if not n:
        return '0 B'
    if n < 1024:
        return '%d B' % n
    if n < 1024 * 1024:
        return '%.1f KB' % (n / 1024.0)
    return '%.1f MB' % (n / 1048576.0)


def diagnose():
    """给界面/命令行用的一句话诊断 + 明细。

    界面在列表为空时直接把 ``hint`` 显示出来，不再是一句「没读到最近文档」——
    到底是没装 WPS、还是格式变了、还是文件被删了，用户能自己判断。
    """
    paths, st = collect(only_exist=False)
    exists = [p for p in paths if os.path.isfile(p)]
    info = {
        'conf': st.get('conf', ''),
        'conf_files': st.get('conf_files', []),
        'structured': st.get('structured', 0),
        'scanned': st.get('scanned', 0),
        'total': len(paths),
        'existing': len(exists),
        'missing': len(paths) - len(exists),
        'fallback': st.get('fallback', ''),
        'fallback_hits': st.get('fallback_hits', 0),
        'opened': st.get('opened', 0),
        'hint': '',
    }

    if not st.get('conf_files'):
        info['hint'] = ('没找到 WPS 的最近文档配置（%s）。确认装的是 WPS for Linux，'
                        '并至少打开过一个文档；也可以用「浏览本地文件…」直接选。'
                        % CONF)
    elif exists:
        if st.get('fallback'):
            info['hint'] = ('WPS 的配置里没读到文档，已改用「%s」兜底，'
                            '共 %d 个。' % (st['fallback'], len(exists)))
        else:
            info['hint'] = '共 %d 个文档。' % len(exists)
    elif paths:
        info['hint'] = ('WPS 记录了 %d 个文档，但文件都不在了（可能被移动、'
                        '删除，或在没挂载的磁盘上）。' % len(paths))
    else:
        info['hint'] = ('WPS 的配置里没解析出任何文档路径 —— 多半是 WPS 升级后'
                        '改了格式。备用数据源（GTK 最近使用记录、云文档目录）'
                        '也没有可用条目；可以先点「浏览本地文件…」复制，'
                        '并把 wps-copy --doctor 的输出发出来。')
    return info


def kind_of(path):
    """给列表用的类型标记：W/X/P/D/云。"""
    if 'WPS Cloud Files' in path or 'WPS 云文档' in path:
        return '云'
    ext = os.path.splitext(path)[1].lower()
    return {
        '.doc': 'W', '.docx': 'W', '.wps': 'W', '.wpt': 'W', '.rtf': 'W',
        '.xls': 'X', '.xlsx': 'X', '.et': 'X', '.ett': 'X', '.csv': 'X',
        '.ppt': 'P', '.pptx': 'P', '.dps': 'P', '.dpt': 'P',
        '.pdf': 'D', '.ofd': 'D',
    }.get(ext, '·')


def source_of(path):
    """来源：云端 / 微信 / 本地。

    放在这里而不是 gui 里，是因为命令行也要用 —— 而命令行不能 import gui
    （那会把 GTK 拖进来，终端里没显示就起不来）。gui 直接引用这个名字。
    """
    low = path.lower()
    if 'wps cloud files' in low or 'wpscloud' in low or '云文档' in path:
        return '云端'
    if 'wechat' in low or 'xwechat_files' in low or '微信' in path:
        return '微信'
    return '本地'


_TYPE_BY_EXT = {
    '.doc': '文档', '.docx': '文档', '.wps': '文档', '.wpt': '文档', '.rtf': '文档',
    '.xls': '表格', '.xlsx': '表格', '.et': '表格', '.ett': '表格', '.csv': '表格',
    '.ppt': '演示', '.pptx': '演示', '.dps': '演示', '.dpt': '演示',
    '.pdf': 'PDF', '.ofd': 'PDF',
    '.txt': '文本', '.md': '文本',
    '.png': '图片', '.jpg': '图片', '.jpeg': '图片', '.gif': '图片',
    '.bmp': '图片', '.webp': '图片',
}

# 筛选下拉用的取值顺序（与列表里能出现的类型一致）
TYPE_CHOICES = ('文档', '表格', '演示', 'PDF', '文本', '图片', '其他')
SOURCE_CHOICES = ('云端', '微信', '本地')


def type_label(path):
    """类型的人话标记：文档 / 表格 / 演示 / PDF / 文本 / 图片 / 其他。"""
    ext = os.path.splitext(path)[1].lower()
    return _TYPE_BY_EXT.get(ext, '其他')


# ---------------------------------------------------------------- 搜索与排序
#
# 放在这里而不是 gui 里：界面和命令行都要用，而命令行不能 import gui（会把
# GTK 拖进来，终端里没显示就起不来）。这几个函数也都不依赖 GTK，能单独测。

def split_terms(text):
    """把搜索框里的一串字切成词，词之间是「并且」关系。

    以前是整串子串匹配：「季度 报告」要求文件名里真有「季度 报告」这几个连着
    的字（还带个空格），结果一个都搜不到。现在按空白切开分别找，和
    ``wps-copy --list 季度 报告`` 的语义对齐。

    str.split() 认所有空白（半角空格、Tab、全角空格 U+3000 都算），
    中文输入法下打出来的空格也就能正常分词。
    """
    if not text:
        return ()
    return tuple(w for w in text.lower().split() if w)


def match_terms(path, terms):
    """所有词都出现在路径里才算命中（文件名或所在目录都算）。"""
    if not terms:
        return True
    low = path.lower()
    return all(t in low for t in terms)


def hit_spans(text, terms):
    """text 里所有命中词的区间，已合并重叠、按起点排序。

    只给区间不生成 markup：gui 那边负责转义和包标签，这里纯字符串处理，
    没有 GTK 也能测。大小写折叠改变长度时（如 'İ'）返回空 —— 索引对不上，
    宁可不高亮也不要把字标错。
    """
    if not text or not terms:
        return []
    low = text.lower()
    if len(low) != len(text):
        return []
    spans = []
    for t in terms:
        k = t.lower()
        if not k:
            continue
        i = 0
        while True:
            hit = low.find(k, i)
            if hit < 0:
                break
            spans.append((hit, hit + len(k)))
            # 跳过一个词的完整长度再找：同一个词连着出现两次要标两段，
            # 但一次命中内部不该再被同一个词切一刀。
            i = hit + len(k)
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


# 表头能点的列：字段名 -> 中文名（状态栏和分组标题都要用）
#
# mtime 这一列显示的是「最近打开」而不是文件修改时间：这两件事在「最近文档」
# 语境里，用户想的是前者（打开一份三个月前的老文档，它就该出现在今天）。
# 没有打开时间记录时才退回文件修改时间。
SORT_LABELS = {
    'name': '名称',
    'source': '来源',
    'type': '类型',
    'size': '大小',
    'mtime': '最近打开',
    'dir': '所在文件夹',
}


def sort_entries(entries, field, reverse=False, opened=None):
    """按 field 排 ``[(path, size_bytes, mtime), ...]``，返回新列表。

    同名/同大小是常事，排序必须可重现，否则每次刷新顺序都在变、用户点不到
    刚才那一行。做法是先按 (文件名, 完整路径) 排一遍当基底，再做稳定排序 ——
    Python 的 sort 是稳定的，主键相同的行就保持基底顺序，升序时不会跟着翻转。

    ``opened`` 是 ``{路径: 打开时间}``：按时间列排序时优先用它，没有记录才用
    文件修改时间。这样点表头和默认视图是同一个尺度，不会点一下就换个顺序。
    """
    if field not in SORT_LABELS:
        raise ValueError('未知的排序列：%r' % (field,))
    opened = opened or {}

    def primary(e):
        p, nbytes, mt = e
        if field == 'name':
            return os.path.basename(p).lower()
        if field == 'source':
            return source_of(p)
        if field == 'type':
            return type_label(p)
        if field == 'size':
            return nbytes or 0
        if field == 'mtime':
            return opened.get(p) or mt or 0
        return os.path.dirname(p).lower()

    rows = sorted(entries, key=lambda e: (os.path.basename(e[0]).lower(), e[0]))
    rows.sort(key=primary, reverse=reverse)
    if field == 'mtime':
        # 档位单独再排一遍（稳定排序）：有打开记录的永远在前，且不受
        # 升序/降序影响 —— 否则点一下「升序」会看到「没记录的文件」跑到最上面。
        rows.sort(key=lambda e: 0 if opened.get(e[0]) else 1)
    return rows


def paginate(paths, limit):
    """按上限切第一页，返回 ``(这一页, 还有多少个没列出)``。

    界面一次最多画 200 条 —— 再多也只是让滚动条变长，该找不到的还是找不到。
    但**过滤要在切页之前做**：只在前 200 条里搜，用户会以为「明明有的文件
    不见了」。所以这里只负责切，切掉多少要能说清楚。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 0
    items = list(paths)
    if limit <= 0 or len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


if __name__ == '__main__':
    for i, p in enumerate(parse_recent(), 1):
        print('%2d. %s' % (i, p))
