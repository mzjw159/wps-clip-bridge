#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""设置窗口：快捷键、主题、托盘行为、开机自启。

设计上所有改动都是「立即生效」而不是等重启：
写入 config.ini → 应用主题 → 重启热键进程 → 切换自启项。
"""
import subprocess

import gi

gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk  # noqa: E402

from . import core, service  # noqa: E402

THEMES = (('system', '跟随系统'), ('light', '浅色'), ('dark', '深色'))

# 项目官网（设置窗口底部会渲染成可点的链接）
SITE_URL = 'https://www.gwbbm.cn'
SITE_TEXT = 'www.gwbbm.cn'

MOD_BITS = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
            | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.SUPER_MASK
            | Gdk.ModifierType.MOD4_MASK)

MOD_KEYS = {
    'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R',
    'Super_L', 'Super_R', 'Meta_L', 'Meta_R', 'Caps_Lock', 'Num_Lock',
    'ISO_Level3_Shift', 'Mode_switch',
}

PRETTY = (('<Control>', 'Ctrl+'), ('<Alt>', 'Alt+'), ('<Shift>', 'Shift+'),
          ('<Super>', 'Super+'))


# ------------------------------------------------------------------ 主题

def system_dark():
    """跟随系统时，判断当前桌面是不是深色。"""
    # 1) 麒麟自己的主题名，最准
    try:
        out = subprocess.run(['gsettings', 'get', 'org.ukui.style', 'style-name'],
                             capture_output=True, text=True, timeout=2).stdout
        low = out.lower()
        if 'dark' in low or 'black' in low:
            return True
        if 'light' in low or 'white' in low:
            return False
    except Exception:
        pass
    # 2) 标准 GNOME 色彩方案
    try:
        out = subprocess.run(
            ['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
            capture_output=True, text=True, timeout=2).stdout
        if 'prefer-dark' in out:
            return True
        if out.strip():
            return False
    except Exception:
        pass
    # 3) 看 GTK 主题名里有没有 dark
    try:
        name = Gtk.Settings.get_default().get_property('gtk-theme-name') or ''
        return 'dark' in name.lower() or 'black' in name.lower()
    except Exception:
        return False


_THEME_DIRS = ('/usr/share/themes', '~/.themes', '~/.local/share/themes')


def _available_themes():
    import os
    found = set()
    for base in _THEME_DIRS:
        base = os.path.expanduser(base)
        try:
            for name in os.listdir(base):
                if os.path.isdir(os.path.join(base, name, 'gtk-3.0')):
                    found.add(name)
        except OSError:
            pass
    return found


def _pick_theme(dark):
    """挑一个主题目录：麒麟只有 ukui-black / ukui-white 两套（没有
    gtk-dark.css 变体），所以靠 prefer-dark 是切不动的，必须换主题名。"""
    have = _available_themes()
    for name in (('ukui-black', 'ukui-dark') if dark else ('ukui-white', 'ukui-light')):
        if name in have:
            return name
    skipped = {'Default', 'Emacs', 'Raleigh'}
    for name in sorted(have - skipped):
        low = name.lower()
        is_dark = ('dark' in low) or ('black' in low)
        if is_dark == dark:
            return name
    return None


def apply_theme(mode=None):
    """应用主题，返回是否最终是深色。"""
    mode = (mode or core.conf('ui', 'theme', 'system')).strip().lower()
    dark = (mode == 'dark') or (mode == 'system' and system_dark())

    name = _pick_theme(dark)
    try:
        st = Gtk.Settings.get_default()
        if name:
            st.set_property('gtk-theme-name', name)
        # 有 gtk-dark.css 变体的主题靠这个开关切，两套机制一起给
        st.set_property('gtk-application-prefer-dark-theme', bool(dark))
    except Exception:
        pass
    core.log('主题：mode=%s dark=%s theme=%s' % (mode, dark, name or '-'))
    return dark


# ------------------------------------------------------------------ 快捷键录入

def pretty_combo(combo):
    """`<Control><Alt>c` -> `Ctrl+Alt+C`，只用于显示。"""
    if not combo:
        return '未设置'
    text = combo
    for a, b in PRETTY:
        text = text.replace(a, b)
    text = text.replace('Control+', 'Ctrl+').replace('ctrl+', 'Ctrl+') \
               .replace('alt+', 'Alt+').replace('shift+', 'Shift+') \
               .replace('super+', 'Super+').replace('win+', 'Super+')
    if text and text[-1:].isalpha() and not text.endswith('+'):
        text = text[:-1] + text[-1].upper()
    return text


class HotkeyButton(Gtk.Button):
    """点一下，按下组合键即录入。至少要求一个 Ctrl/Alt/Super 修饰键。"""

    def __init__(self, combo=''):
        Gtk.Button.__init__(self)
        self.combo = combo or ''
        self._capturing = False
        self._handler = None
        self._refresh()
        self.connect('clicked', self._start_capture)

    def _refresh(self):
        if self._capturing:
            self.set_label('请按下组合键…（Esc 取消）')
        else:
            self.set_label(pretty_combo(self.combo))

    def _start_capture(self, *_):
        if self._capturing:
            return
        self._capturing = True
        self._refresh()
        top = self.get_toplevel()
        self._handler = top.connect('key-press-event', self._on_key, top)

    def _stop_capture(self):
        self._capturing = False
        top = self.get_toplevel()
        if self._handler is not None and top is not None:
            try:
                top.disconnect(self._handler)
            except Exception:
                pass
        self._handler = None
        self._refresh()

    def _on_key(self, _w, event, top):
        if not self._capturing:
            return False
        name = Gdk.keyval_name(event.keyval)
        if name is None:
            return True
        if name == 'Escape':
            self._stop_capture()
            return True
        if name in ('BackSpace', 'Delete'):
            self.combo = ''
            self._stop_capture()
            return True
        if name in MOD_KEYS:
            # 只按了修饰键，继续等真正的按键
            return True

        mods = event.state & MOD_BITS
        mask = 0
        if mods & Gdk.ModifierType.CONTROL_MASK:
            mask |= 0x04
        if mods & Gdk.ModifierType.MOD1_MASK:
            mask |= 0x08
        if mods & Gdk.ModifierType.SHIFT_MASK:
            mask |= 0x01
        if mods & Gdk.ModifierType.SUPER_MASK:
            mask |= 0x40
        if mask == 0:
            # 没有 Ctrl/Alt/Super 会把整个键盘抓走，不允许
            self.set_label('需要搭配 Ctrl / Alt / Super')
            return True

        parts = []
        if mods & Gdk.ModifierType.CONTROL_MASK:
            parts.append('<Control>')
        if mods & Gdk.ModifierType.MOD1_MASK:
            parts.append('<Alt>')
        if mods & Gdk.ModifierType.SHIFT_MASK:
            parts.append('<Shift>')
        if mods & Gdk.ModifierType.SUPER_MASK:
            parts.append('<Super>')

        key = name.lower() if len(name) == 1 else name
        self.combo = ''.join(parts) + key
        self._stop_capture()
        return True


# ------------------------------------------------------------------ 设置窗口

class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent=None):
        Gtk.Dialog.__init__(self, title='设置', parent=parent, modal=True)
        self.set_default_size(560, -1)
        self.set_border_width(0)
        self.add_button('恢复默认', Gtk.ResponseType.APPLY)
        self.add_button('取消', Gtk.ResponseType.CANCEL)
        save = self.add_button('保存', Gtk.ResponseType.OK)
        save.get_style_context().add_class('suggested-action')

        box = self.get_content_area()
        box.set_spacing(14)
        box.set_border_width(18)

        # ---- 快捷键
        box.pack_start(self._section('全局快捷键'), False, False, 0)
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.set_margin_start(12)

        self.hk_enabled = Gtk.CheckButton(label='启用全局快捷键')
        self.hk_enabled.set_active(core.conf_bool('hotkey', 'enabled', True))
        grid.attach(self.hk_enabled, 0, 0, 2, 1)

        self.btn_combo = HotkeyButton(core.conf('hotkey', 'combo', '<Control><Alt>c'))
        grid.attach(self._label('唤起窗口'), 0, 1, 1, 1)
        grid.attach(self.btn_combo, 1, 1, 1, 1)

        self.copy_enabled = Gtk.CheckButton(label='启用')
        self.copy_enabled.set_active(core.conf_bool('hotkey', 'copy_enabled', True))
        self.btn_copy_combo = HotkeyButton(
            core.conf('hotkey', 'copy_combo', '<Control><Shift>c'))
        row = Gtk.Box(spacing=8)
        row.pack_start(self.btn_copy_combo, False, False, 0)
        row.pack_start(self.copy_enabled, False, False, 0)
        grid.attach(self._label('复制最近文档'), 0, 2, 1, 1)
        grid.attach(row, 1, 2, 1, 1)

        hint = Gtk.Label()
        hint.set_markup('<span size="small">点按钮后直接按下组合键（至少带一个修饰键）；'
                        'Delete 键清空，Esc 取消。</span>')
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class('dim-label')
        grid.attach(hint, 0, 3, 2, 1)
        box.pack_start(grid, False, False, 0)

        # ---- 外观
        box.pack_start(self._section('外观'), False, False, 0)
        grid2 = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid2.set_margin_start(12)

        self.theme_combo = Gtk.ComboBoxText()
        cur = core.conf('ui', 'theme', 'system').strip().lower()
        for i, (key, label) in enumerate(THEMES):
            self.theme_combo.append(key, label)
            if key == cur:
                self.theme_combo.set_active(i)
        if self.theme_combo.get_active() < 0:
            self.theme_combo.set_active(0)
        grid2.attach(self._label('主题'), 0, 0, 1, 1)
        grid2.attach(self.theme_combo, 1, 0, 1, 1)

        self.limit_spin = Gtk.SpinButton.new_with_range(20, 500, 10)
        self.limit_spin.set_value(core.conf_int('ui', 'limit', 80))
        grid2.attach(self._label('列表最多显示'), 0, 1, 1, 1)
        grid2.attach(self.limit_spin, 1, 1, 1, 1)
        box.pack_start(grid2, False, False, 0)

        # ---- 行为
        box.pack_start(self._section('窗口与托盘'), False, False, 0)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(12)

        self.chk_tray = Gtk.CheckButton(label='关闭窗口时收进系统托盘，不退出程序')
        self.chk_tray.set_active(core.conf_bool('ui', 'close_to_tray', True))
        vbox.pack_start(self.chk_tray, False, False, 0)

        self.chk_hide_after = Gtk.CheckButton(label='复制成功后自动收起窗口')
        self.chk_hide_after.set_active(core.conf_bool('ui', 'hide_after_copy', True))
        vbox.pack_start(self.chk_hide_after, False, False, 0)

        self.chk_start_hidden = Gtk.CheckButton(label='启动时直接待在托盘，不弹出窗口')
        self.chk_start_hidden.set_active(core.conf_bool('ui', 'start_hidden', False))
        vbox.pack_start(self.chk_start_hidden, False, False, 0)

        self.chk_notify = Gtk.CheckButton(label='复制成功后发送桌面通知')
        self.chk_notify.set_active(core.conf_bool('ui', 'notify_on_copy', True))
        vbox.pack_start(self.chk_notify, False, False, 0)

        # 托盘图标样式：StatusNotifier 协议里，面板对左键的处理一律是弹菜单
        # （程序没法改），«点一下图标就显示窗口» 只有经典图标能做到。留个开关。
        row_backend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_backend.pack_start(self._label('托盘图标样式'), False, False, 0)
        self.cmb_backend = Gtk.ComboBoxText()
        for key, text in (('auto', '自动（推荐）'),
                          ('indicator', '跟随面板（左键弹菜单）'),
                          ('statusicon', '经典图标（点一下即显示窗口）')):
            self.cmb_backend.append(key, text)
        cur_backend = core.conf('ui', 'tray_backend', 'auto').strip().lower()
        self.cmb_backend.set_active_id(
            cur_backend if cur_backend in ('auto', 'indicator', 'statusicon')
            else 'auto')
        row_backend.pack_start(self.cmb_backend, False, False, 0)
        vbox.pack_start(row_backend, False, False, 0)
        box.pack_start(vbox, False, False, 0)

        # ---- 启动
        box.pack_start(self._section('启动'), False, False, 0)
        self.chk_autostart = Gtk.CheckButton(label='开机自动启动后台服务')
        self.chk_autostart.set_active(core.autostart_enabled())
        self.chk_autostart.set_margin_start(12)
        box.pack_start(self.chk_autostart, False, False, 0)

        # ---- 关于
        box.pack_start(self._section('关于'), False, False, 0)
        about = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        about.set_margin_start(12)

        made = Gtk.Label()
        made.set_markup('<b>公文帮帮忙</b> 出品')
        made.set_xalign(0)
        about.pack_start(made, False, False, 0)

        ver = Gtk.Label(label='WPS 最近文档复制  v%s' % core.VERSION)
        ver.set_xalign(0)
        ver.get_style_context().add_class('dim-label')
        about.pack_start(ver, False, False, 0)

        # 真链接而不是纯文字：Gtk.LinkButton 点一下就用默认浏览器打开
        site = Gtk.LinkButton.new_with_label(SITE_URL, '官网  ' + SITE_TEXT)
        site.set_halign(Gtk.Align.START)
        site.set_tooltip_text(SITE_URL)
        about.pack_start(site, False, False, 0)

        tip = Gtk.Label(label='把 WPS 里的最近文档变成能直接粘贴的文件，'
                              '省掉翻文件夹那一步。\n用着不顺手、想要什么功能，'
                              '都欢迎到官网找我们。')
        tip.set_xalign(0)
        tip.set_line_wrap(True)
        tip.set_margin_top(4)
        tip.get_style_context().add_class('dim-label')
        about.pack_start(tip, False, False, 0)
        box.pack_start(about, False, False, 0)

        self.error = Gtk.Label()
        self.error.set_xalign(0)
        self.error.set_line_wrap(True)
        self.error.get_style_context().add_class('error')
        box.pack_start(self.error, False, False, 0)

        self.show_all()

    @staticmethod
    def _label(text):
        lab = Gtk.Label(label=text)
        lab.set_xalign(0)
        return lab

    @staticmethod
    def _section(title):
        lab = Gtk.Label()
        lab.set_markup('<b>%s</b>' % title)
        lab.set_xalign(0)
        lab.set_margin_top(4)
        return lab

    # --------------------------------------------------------------- 取值
    def values(self):
        return {
            'hotkey_enabled': self.hk_enabled.get_active(),
            'combo': self.btn_combo.combo.strip(),
            'copy_enabled': self.copy_enabled.get_active(),
            'copy_combo': self.btn_copy_combo.combo.strip(),
            'theme': THEMES[max(0, self.theme_combo.get_active())][0],
            'limit': int(self.limit_spin.get_value()),
            'close_to_tray': self.chk_tray.get_active(),
            'hide_after_copy': self.chk_hide_after.get_active(),
            'start_hidden': self.chk_start_hidden.get_active(),
            'notify_on_copy': self.chk_notify.get_active(),
            'tray_backend': self.cmb_backend.get_active_id() or 'auto',
            'autostart': self.chk_autostart.get_active(),
        }

    def validate(self, v):
        from .hotkey import parse_combo
        if v['hotkey_enabled']:
            if not v['combo']:
                return '唤起窗口的快捷键不能为空（也可以先关掉「启用全局快捷键」）'
            if not parse_combo(v['combo']):
                return '唤起窗口的快捷键“%s”无法识别' % v['combo']
        if v['hotkey_enabled'] and v['copy_enabled']:
            if v['combo'] and v['combo'] == v['copy_combo']:
                return '两个快捷键不能相同'
            if not v['copy_combo']:
                return '复制最近文档的快捷键不能为空'
            if not parse_combo(v['copy_combo']):
                return '复制最近文档的快捷键“%s”无法识别' % v['copy_combo']
        return ''

    # --------------------------------------------------------------- 应用
    def apply(self, v):
        core.save_conf({
            ('hotkey', 'enabled'): 'true' if v['hotkey_enabled'] else 'false',
            ('hotkey', 'combo'): v['combo'],
            ('hotkey', 'copy_enabled'): 'true' if v['copy_enabled'] else 'false',
            ('hotkey', 'copy_combo'): v['copy_combo'],
            ('ui', 'theme'): v['theme'],
            ('ui', 'limit'): v['limit'],
            ('ui', 'close_to_tray'): 'true' if v['close_to_tray'] else 'false',
            ('ui', 'hide_after_copy'): 'true' if v['hide_after_copy'] else 'false',
            ('ui', 'start_hidden'): 'true' if v['start_hidden'] else 'false',
            ('ui', 'notify_on_copy'): 'true' if v['notify_on_copy'] else 'false',
            ('ui', 'tray_backend'): v['tray_backend'],
        })
        apply_theme(v['theme'])
        service.restart_hotkey()
        # 托盘后端改了要重建图标，否则得等下次启动才生效
        parent = self.get_transient_for()
        if hasattr(parent, 'reload_tray'):
            try:
                parent.reload_tray()
            except Exception:
                pass
        try:
            core.set_autostart(v['autostart'])
        except OSError:
            pass
        return True

    def restore_defaults(self):
        d = core.DEFAULTS
        self.hk_enabled.set_active(d['hotkey']['enabled'] == 'true')
        self.btn_combo.combo = d['hotkey']['combo']
        self.btn_combo._refresh()
        self.copy_enabled.set_active(d['hotkey']['copy_enabled'] == 'true')
        self.btn_copy_combo.combo = d['hotkey']['copy_combo']
        self.btn_copy_combo._refresh()
        for i, (key, _l) in enumerate(THEMES):
            if key == d['ui']['theme']:
                self.theme_combo.set_active(i)
        self.limit_spin.set_value(int(d['ui']['limit']))
        self.chk_tray.set_active(d['ui']['close_to_tray'] == 'true')
        self.chk_hide_after.set_active(d['ui']['hide_after_copy'] == 'true')
        self.chk_start_hidden.set_active(d['ui']['start_hidden'] == 'true')
        self.chk_notify.set_active(d['ui']['notify_on_copy'] == 'true')
        self.cmb_backend.set_active_id(d['ui'].get('tray_backend', 'auto'))


def open_settings(parent=None):
    """弹出设置窗口；保存成功后返回 True。"""
    dlg = SettingsDialog(parent)
    saved = False
    while True:
        resp = dlg.run()
        if resp == Gtk.ResponseType.APPLY:
            dlg.restore_defaults()
            dlg.error.set_text('')
            continue
        if resp == Gtk.ResponseType.OK:
            v = dlg.values()
            err = dlg.validate(v)
            if err:
                dlg.error.set_text(err)
                continue
            try:
                dlg.apply(v)
            except Exception as ex:
                dlg.error.set_text('保存失败：%s' % ex)
                continue
            saved = True
        break
    dlg.destroy()
    return saved
