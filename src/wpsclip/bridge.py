#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""常驻剪贴板服务。

做两件事：

1. **补救**：有些程序（如某些文件管理器/代理）复制文件时只写
   ``x-special/gnome-copied-files`` 而漏了标准的 ``text/uri-list``，
   或者把 ``copy`` 头行塞进了 ``text/uri-list``（非法）。本服务发现后
   读出真实路径、接管 selection，同时供应全部格式。

2. **代发**：其它入口（GUI / 命令行 / 热键）把待复制路径写进请求文件，
   由本服务把文件放进剪贴板 —— 因为 selection 需要长期持有者，
   而工具窗口关掉后不能影响粘贴。

普通文本、图片、以及本来就合法的文件剪贴板，一律不碰。
"""
import os
import select
import time
import urllib.parse

from Xlib import X, Xatom, display
from Xlib.protocol import event as xev

from . import core

POLL = core.conf_float('bridge', 'poll', 0.3)


class Bridge(object):
    FILE_TARGETS = {
        'x-special/gnome-copied-files',
        'x-special/mate-copied-files',
        'text/uri-list',
        'application/x-kfilelistcustomcontentlist',
    }

    def __init__(self, display_name=None):
        name = display_name or core.conf('bridge', 'display', '') or None
        self.d = display.Display(name)
        scr = self.d.screen()
        self.win = scr.root.create_window(0, 0, 1, 1, 0, scr.root_depth)
        self.A = {}

        def atom(n):
            a = self.d.intern_atom(n)
            self.A[n] = a
            return a

        for n in ('CLIPBOARD', 'TARGETS', 'TIMESTAMP', 'UTF8_STRING', 'TEXT',
                  'text/plain', 'text/plain;charset=utf-8', 'text/uri-list',
                  'x-special/gnome-copied-files', 'x-special/mate-copied-files',
                  'application/x-kfilelistcustomcontentlist',
                  '_WPS_CLIP_BRIDGE_TMP'):
            atom(n)

        self.CLIPBOARD = self.A['CLIPBOARD']
        self.TARGETS = self.A['TARGETS']
        self.TMP = self.A['_WPS_CLIP_BRIDGE_TMP']

        self.payloads = {}
        self.mode = 'copy'
        self.last_sig = None
        self.last_pub_ts = 0.0
        self.last_paths = None
        self.pub_hist = []
        self.last_owner_seen = None
        self.last_scan_ts = 0.0
        self.is_owner = False

        # 启动时忽略历史请求，避免把上次复制的文件又塞回剪贴板
        self.req_mtime = core.mtime(core.req_path())

    # ------------------------------------------------ 读取别人的 selection
    def fetch_prop(self, target, timeout=0.6):
        try:
            self.win.delete_property(self.TMP)
        except Exception:
            pass
        self.win.convert_selection(self.CLIPBOARD, target, self.TMP, X.CurrentTime)
        self.d.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.d.pending_events():
                e = self.d.next_event()
                if (e.type == X.SelectionNotify and e.selection == self.CLIPBOARD
                        and e.target == target):
                    if e.property == X.NONE:
                        return None
                    try:
                        return self.win.get_full_property(self.TMP, X.AnyPropertyType)
                    except Exception:
                        return None
            else:
                time.sleep(0.01)
        return None

    def read_names(self):
        r = self.fetch_prop(self.TARGETS)
        if r is None or r.format != 32:
            return None
        names = set()
        for a in list(r.value):
            try:
                names.add(self.d.get_atom_name(a))
            except Exception:
                pass
        return names

    def read_text(self, name):
        r = self.fetch_prop(self.A[name])
        if r is None:
            return None
        v = r.value
        return v.decode('utf-8', 'replace') if isinstance(v, bytes) else None

    @staticmethod
    def parse_uris(text):
        if not text:
            return []
        out, seen = [], set()
        for line in text.replace('\r\n', '\n').split('\n'):
            line = line.strip()
            if not line or line in ('copy', 'cut') or line.startswith('#'):
                continue
            if line.startswith('file://'):
                p = urllib.parse.unquote(line[7:])
            elif line.startswith('/'):
                p = line
            else:
                continue
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    # ------------------------------------------------ 发布
    def publish(self, paths, mode='copy'):
        self.mode = 'cut' if mode == 'cut' else 'copy'
        uris = ['file://' + urllib.parse.quote(p, safe='/') for p in paths]
        b_text = ('\n'.join(paths)).encode('utf-8')
        b_uri = ('\r\n'.join(uris) + '\r\n').encode('utf-8')
        b_gnome = (self.mode + '\n' + '\n'.join(uris) + '\n').encode('utf-8')

        self.payloads = {
            self.A['UTF8_STRING']: b_text,
            self.A['TEXT']: b_text,
            self.A['text/plain']: b_text,
            self.A['text/plain;charset=utf-8']: b_text,
            self.A['text/uri-list']: b_uri,
            self.A['x-special/gnome-copied-files']: b_gnome,
            self.A['x-special/mate-copied-files']: b_gnome,
        }
        self.win.set_selection_owner(self.CLIPBOARD, X.CurrentTime)
        self.is_owner = True
        self.d.flush()
        core.log('published %s %d path(s): %r' % (self.mode, len(paths), paths))
        # 通知等待者（CLI 写完请求后轮询这个文件的 mtime）
        core.touch(core.runtime_file('published'))

    def handle_request(self, e):
        prop = e.property or e.target
        try:
            tname = self.d.get_atom_name(e.target)
        except Exception:
            tname = str(e.target)
        try:
            if e.target == self.TARGETS:
                atoms = [self.TARGETS, self.A['TIMESTAMP'], self.A['UTF8_STRING'],
                         self.A['TEXT'], self.A['text/plain'],
                         self.A['text/plain;charset=utf-8'],
                         self.A['text/uri-list'],
                         self.A['x-special/gnome-copied-files'],
                         self.A['x-special/mate-copied-files']]
                e.requestor.change_property(prop, Xatom.ATOM, 32, atoms)
                core.log('REQ targets <- %s' % tname)
            elif e.target == self.A['TIMESTAMP']:
                e.requestor.change_property(prop, Xatom.INTEGER, 32,
                                            [int(e.time) & 0xFFFFFFFF])
            elif e.target in self.payloads:
                e.requestor.change_property(prop, e.target, 8, self.payloads[e.target])
                kind = 'text' if b'file://' not in self.payloads[e.target] else 'FILE-URI'
                core.log('REQ %s <- %s (%d bytes)'
                         % (kind, tname, len(self.payloads[e.target])))
            else:
                prop = X.NONE
                core.log('REQ %s <- REFUSED' % tname)
        except Exception as ex:
            core.log('handle_request err %r' % ex)
            prop = X.NONE
        try:
            ev = xev.SelectionNotify(time=e.time, requestor=e.requestor,
                                     selection=e.selection, target=e.target,
                                     property=prop)
            self.d.send_event(e.requestor, ev)
            self.d.flush()
        except Exception as ex:
            core.log('send notify err %r' % ex)

    # ------------------------------------------------ 巡检
    def uri_list_valid(self, names):
        """现成的 text/uri-list 是否真的可用。

        有些代理会把 x-special 的内容原样塞进 text/uri-list（多出 copy/cut 行），
        这不合规范，桌面端解析会失败、粘贴变灰，所以也算需要修复。
        """
        if 'text/uri-list' not in names:
            return False
        txt = self.read_text('text/uri-list')
        if not txt or not txt.strip():
            return False
        for line in txt.replace('\r\n', '\n').split('\n'):
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s in ('copy', 'cut'):
                return False
            if not (s.startswith('file://') or s.startswith('/')):
                return False
        return True

    def throttle(self):
        """限流，避免与合成器的剪贴板桥来回抢 owner 造成抖动。"""
        now = time.time()
        self.pub_hist = [t for t in self.pub_hist if now - t < 5.0]
        if len(self.pub_hist) >= 5:
            return False
        if self.pub_hist and now - self.pub_hist[-1] < 0.6:
            return False
        self.pub_hist.append(now)
        return True

    def poll_once(self):
        owner = self.d.get_selection_owner(self.CLIPBOARD)
        try:
            owner_id = owner.id if hasattr(owner, 'id') else int(owner)
        except Exception:
            owner_id = 0
        if owner_id == self.win.id:
            self.is_owner = True
            return
        self.is_owner = False

        # owner 变了立刻看；没变则最多每秒看一次，少打扰持有者
        changed = (owner_id != self.last_owner_seen)
        now = time.time()
        if changed:
            self.last_owner_seen = owner_id
        elif now - self.last_scan_ts < 1.0:
            return
        self.last_scan_ts = now

        names = self.read_names()
        if names is None:
            if changed:
                core.log('owner=0x%x 读取 TARGETS 超时/失败' % owner_id)
            return
        if changed:
            core.log('owner=0x%x targets=%s' % (owner_id, sorted(names)))

        if not names or not (names & self.FILE_TARGETS):
            return
        if self.uri_list_valid(names):
            return

        mode = 'copy'
        paths = []
        for src in ('x-special/gnome-copied-files', 'x-special/mate-copied-files',
                    'application/x-kfilelistcustomcontentlist', 'text/uri-list'):
            if src in names:
                txt = self.read_text(src)
                if txt and txt.lstrip().split('\n', 1)[0].strip() == 'cut':
                    mode = 'cut'
                p = self.parse_uris(txt)
                if p:
                    paths = p
                    core.log('paths from [%s]: %r' % (src, p))
                    break
        if not paths:
            core.log('file-copy detected but no path parsed; targets=%s'
                     % sorted(names))
            return

        sig = (mode, tuple(paths))
        if sig == self.last_sig and time.time() - self.last_pub_ts < 0.6:
            return
        if not self.throttle():
            return
        self.last_sig = sig
        self.last_paths = paths
        self.last_pub_ts = time.time()
        self.publish(paths, mode)

    def dispatch(self, e):
        try:
            if e.type == X.SelectionRequest:
                self.handle_request(e)
            elif e.type == X.SelectionClear:
                if getattr(e, 'selection', None) == self.CLIPBOARD:
                    self.is_owner = False
        except Exception as ex:
            core.log('dispatch err %r' % ex)

    def check_request(self):
        """外部入口写请求文件 -> 本服务代发。"""
        cur = core.mtime(core.req_path())
        if cur is None or cur == self.req_mtime:
            return
        self.req_mtime = cur
        try:
            with open(core.req_path(), 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f if l.strip()]
        except OSError:
            return
        paths = [p for p in lines if p.startswith('/') and len(p) > 1]
        missing = [p for p in paths if not _exists(p)]
        paths = [p for p in paths if _exists(p)]
        if missing:
            core.log('request: %d path(s) missing, skipped' % len(missing))
        if not paths:
            core.log('request ignored, no usable path: %r' % lines)
            return
        self.publish(paths, 'copy')
        core.log('published by request: %r' % paths)

    def run(self):
        core.log('bridge started pid=%d display=%s'
                 % (os.getpid(), self.d.get_display_name()))
        fd = self.d.fileno()
        while True:
            try:
                while self.d.pending_events():
                    self.dispatch(self.d.next_event())
                try:
                    r, _, _ = select.select([fd], [], [], POLL)
                except Exception:
                    r = []
                if r:
                    while self.d.pending_events():
                        self.dispatch(self.d.next_event())
                self.check_request()
                self.poll_once()
            except Exception as ex:
                core.log('loop err %r' % ex)
                time.sleep(0.5)


def _exists(p):
    return os.path.exists(p)


def main(argv=None):
    import sys

    si = core.SingleInstance('bridge')
    if not si.acquire():
        core.log('另一个剪贴板服务已在运行，本次退出')
        return 0

    core.log('%s %s 启动 (pid=%d)' % (core.APP, core.VERSION, os.getpid()))
    try:
        while True:
            try:
                Bridge().run()
            except Exception as ex:
                core.log('显示连接中断 %r，3 秒后重试' % ex)
                time.sleep(3)
    except KeyboardInterrupt:
        core.log('收到 SIGINT，退出')
    finally:
        si.release()
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
