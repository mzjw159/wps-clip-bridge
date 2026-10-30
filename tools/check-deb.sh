#!/bin/sh
# 检查打出来的 .deb 里该有的东西在不在、该统一的有没有统一。
#
#   ./tools/check-deb.sh                     检查 dist/ 下的包（必须恰好一个）
#   ./tools/check-deb.sh path/to/x.deb       检查指定的包
#
# CI 每次构建都会跑一遍；本地改完 build.sh 或 data/ 下的东西也可以先跑它。
# 检查项都是「漏了会真出问题」的：装完没有菜单项、名字还是旧的、图标没进包、
# 可执行位丢了…… 这些在本地看着正常，装到别人机器上才暴露。
#
# 刻意不解包到磁盘（不用 dpkg-deb -x）：一是没必要，二是在禁 utime 的
# 受限环境里解包会失败（tar 想恢复文件时间戳），读内容用 tar -xO 就够了。
set -e

HERE=$(cd "$(dirname "$0")/.." && pwd)
DEB="$1"
if [ -z "$DEB" ]; then
    # 通配成多个时不猜，直接报错，免得检查了错的那个
    set -- "$HERE"/dist/wps-clip-bridge_*_all.deb
    [ "$#" -eq 1 ] || { echo "dist/ 下有 $# 个 deb，请指定要检查哪个" >&2; exit 2; }
    DEB="$1"
fi
[ -f "$DEB" ] || { echo "找不到 $DEB" >&2; exit 2; }

FAIL=0
ok()  { echo "  ✔ $*"; }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

# 期望值从仓库里读，不写死在脚本里，改版本号/名称时不用同步改这里
WANT_NAME=$(sed -n 's/^Name=//p' "$HERE/data/wps-clip-bridge.desktop" | head -1)
WANT_VERSION=$(sed -n 's/^Version:[[:space:]]*//p' "$HERE/debian/control" | head -1)

# 包里的文件清单（含权限位），以及数据段 tar 的本地副本，后面反复读
LISTING=$(dpkg-deb -c "$DEB")
TARBALL="$HERE/build/check-deb.tar"
mkdir -p "$HERE/build"
dpkg-deb --fsys-tarfile "$DEB" > "$TARBALL" || { echo "读不出数据段：$DEB" >&2; exit 1; }
trap 'rm -f "$TARBALL"' EXIT INT TERM

# 打印包内某个文件的内容。找不到就是空，不报错
member() { tar -xO -f "$TARBALL" "./$1" 2>/dev/null || true; }

echo "==> 包：$(basename "$DEB")"

echo "包信息"
INFO=$(dpkg-deb -f "$DEB")
printf '%s\n' "$INFO" | grep -qx "Version: $WANT_VERSION" \
    && ok "Version = $WANT_VERSION" || bad "Version 与 debian/control 不一致（期望 $WANT_VERSION）"
printf '%s\n' "$INFO" | grep -qx 'Architecture: all' \
    && ok "Architecture = all（换架构也能装）" || bad "Architecture 不是 all"
ISIZE=$(printf '%s\n' "$INFO" | sed -n 's/^Installed-Size:[[:space:]]*//p')
[ "${ISIZE:-0}" -gt 0 ] && ok "Installed-Size = ${ISIZE} KB" || bad "Installed-Size 没回填"

echo "文件清单"
for f in \
    usr/bin/wps-copy \
    usr/lib/python3/dist-packages/wpsclip/__init__.py \
    usr/lib/python3/dist-packages/wpsclip/gui.py \
    usr/lib/python3/dist-packages/wpsclip/sni.py \
    usr/share/applications/wps-clip-bridge.desktop \
    usr/share/metainfo/wps-clip-bridge.metainfo.xml \
    usr/share/icons/hicolor/scalable/apps/wps-file.svg \
    usr/share/icons/hicolor/256x256/apps/wps-file.png \
    usr/share/icons/hicolor/256x256/apps/wps-clip-bridge.png \
    usr/share/pixmaps/wps-file.png \
    etc/xdg/autostart/wps-clip-bridge.desktop \
    usr/share/man/man1/wps-copy.1.gz \
    usr/share/doc/wps-clip-bridge/copyright
do
    if printf '%s\n' "$LISTING" | grep -q " \./$f\$"; then
        ok "$f"
    else
        bad "缺文件：$f"
    fi
done

echo "可执行位"
# 数据段里的二进制
for f in usr/bin/wps-copy; do
    if printf '%s\n' "$LISTING" | grep -q "^-rwxr.xr.x .* \./$f\$"; then
        ok "$f 可执行"
    else
        bad "$f 没有执行权限"
    fi
done
# 控制脚本在控制段里，dpkg-deb -c / -I 都看不到权限位，得单独列控制段的 tar
CTRL=$(dpkg-deb --ctrl-tarfile "$DEB" | tar -tvf - 2>/dev/null || true)
for f in postinst prerm postrm; do
    printf '%s\n' "$CTRL" | grep -q "^-rwx.* \./$f\$" \
        && ok "DEBIAN/$f 可执行" || bad "DEBIAN/$f 没有执行权限"
done

echo "菜单项名称"
INSTALLED_NAME=$(member usr/share/applications/wps-clip-bridge.desktop \
    | sed -n 's/^Name=//p' | head -1)
[ "$INSTALLED_NAME" = "$WANT_NAME" ] \
    && ok "Name = $INSTALLED_NAME" || bad "Name 是「$INSTALLED_NAME」，期望「$WANT_NAME」"

# 同一个程序两种叫法是专门修过的，别又长回来。逐个读会显示应用名的文件
STALE=""
for f in \
    usr/share/applications/wps-clip-bridge.desktop \
    usr/share/metainfo/wps-clip-bridge.metainfo.xml \
    etc/xdg/autostart/wps-clip-bridge.desktop \
    usr/lib/python3/dist-packages/wpsclip/sni.py \
    usr/lib/python3/dist-packages/wpsclip/tray.py \
    usr/lib/python3/dist-packages/wpsclip/settings.py \
    usr/lib/python3/dist-packages/wpsclip/service.py
do
    if member "$f" | grep -q "WPS 最近文档复制"; then
        STALE="$STALE $f"
    fi
done
if [ -n "$STALE" ]; then
    bad "还有旧名「WPS 最近文档复制」：$STALE"
else
    ok "没有旧名「WPS 最近文档复制」残留"
fi

# 菜单项 Exec 指向的二进制必须在包里，否则点了什么都不发生
EXEC=$(member usr/share/applications/wps-clip-bridge.desktop \
    | sed -n 's/^Exec=//p' | head -1 | awk '{print $1}')
if printf '%s\n' "$LISTING" | grep -q " \./usr/bin/$EXEC\$"; then
    ok "Exec=$EXEC 在包里"
else
    bad "Exec 指向的 $EXEC 不在包里"
fi

# 图标名要和 desktop 里的 Icon= 对得上（差一个字符就是系统默认齿轮图标）
ICON=$(member usr/share/applications/wps-clip-bridge.desktop \
    | sed -n 's/^Icon=//p' | head -1)
printf '%s\n' "$LISTING" | grep -q " \./usr/share/pixmaps/$ICON\.png\$" \
    && ok "Icon=$ICON 在 pixmaps 里有对应文件" || bad "Icon=$ICON 在 pixmaps 里找不到"

echo ""
if [ "$FAIL" -gt 0 ]; then
    echo "包检查失败：$FAIL 项" >&2
    exit 1
fi
echo "包检查通过。"
