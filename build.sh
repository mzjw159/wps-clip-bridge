#!/bin/sh
# 构建 wps-clip-bridge 的 .deb 安装包。
#
#   ./build.sh            构建到本项目的 dist/
#   ./build.sh --check    构建后额外做一致性检查
#
# 可用环境变量覆盖：OUT=/path/to/dir ./build.sh
#
# 依赖：dpkg-deb、gzip。不需要 fakeroot（用 --root-owner-group）。
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/src"
DATA="$HERE/data"
DEB="$HERE/debian"
BUILD="$HERE/build"
OUT="${OUT:-$HERE/dist}"

VERSION=$(sed -n 's/^Version:[[:space:]]*//p' "$DEB/control" | head -1)
PKG=$(sed -n 's/^Package:[[:space:]]*//p' "$DEB/control" | head -1)
ARCH=$(sed -n 's/^Architecture:[[:space:]]*//p' "$DEB/control" | head -1)
NAME="${PKG}_${VERSION}_${ARCH}"

die() { echo "构建失败：$*" >&2; exit 1; }

command -v dpkg-deb >/dev/null 2>&1 || die "缺少 dpkg-deb"

echo "==> 清理"
rm -rf "$BUILD"
mkdir -p "$BUILD/$NAME" "$OUT"

R="$BUILD/$NAME"

echo "==> 安装 Python 包"
install -d -m 755 "$R/usr/lib/python3/dist-packages/wpsclip"
for f in "$SRC"/wpsclip/*.py; do
    install -m 644 "$f" "$R/usr/lib/python3/dist-packages/wpsclip/"
done

echo "==> 安装可执行入口"
install -d -m 755 "$R/usr/bin"
for f in "$HERE"/bin/*; do
    install -m 755 "$f" "$R/usr/bin/$(basename "$f")"
done

echo "==> 安装菜单项与图标"
install -d -m 755 "$R/usr/share/applications"
install -m 644 "$DATA/wps-clip-bridge.desktop" "$R/usr/share/applications/"

# 矢量图标（任意尺寸都清晰）
install -d -m 755 "$R/usr/share/icons/hicolor/scalable/apps"
install -m 644 "$DATA/wps-file.svg" "$R/usr/share/icons/hicolor/scalable/apps/"

# 常见尺寸的光栅图标（部分面板/桌面环境只认固定尺寸 PNG）
for sz in 48 64 128 256 512; do
    src="$DATA/icons/hicolor/${sz}x${sz}/apps/wps-file.png"
    [ -f "$src" ] || die "缺少图标：$src"
    install -d -m 755 "$R/usr/share/icons/hicolor/${sz}x${sz}/apps"
    install -m 644 "$src" "$R/usr/share/icons/hicolor/${sz}x${sz}/apps/"
done

# 别名：同一套图标再以「包名」wps-clip-bridge 装一份。
# 麒麟安装器/软件中心是按包名/desktop 名去图标主题里查的
# （QIcon::fromTheme(<名字>)），只装 wps-file 时按包名一查就落空，
# 只能显示系统的通用/齿轮图标。两个名字都装，两条查找路径都能命中。
for sz in 48 64 128 256 512; do
    install -m 644 "$DATA/icons/hicolor/${sz}x${sz}/apps/wps-file.png" \
        "$R/usr/share/icons/hicolor/${sz}x${sz}/apps/wps-clip-bridge.png"
done
install -m 644 "$DATA/wps-file.svg" \
    "$R/usr/share/icons/hicolor/scalable/apps/wps-clip-bridge.svg"

# /usr/share/pixmaps：只认 pixmaps 的老面板、以及部分安装器会兜底查这里
install -d -m 755 "$R/usr/share/pixmaps"
install -m 644 "$DATA/icons/hicolor/256x256/apps/wps-file.png" "$R/usr/share/pixmaps/wps-file.png"
install -m 644 "$DATA/icons/hicolor/256x256/apps/wps-file.png" "$R/usr/share/pixmaps/wps-clip-bridge.png"
install -m 644 "$DATA/wps-file.svg" "$R/usr/share/pixmaps/wps-file.svg"

# AppStream 元数据：软件中心/应用商店类前端靠它认领图标与名称
if [ -f "$DATA/wps-clip-bridge.metainfo.xml" ]; then
    install -d -m 755 "$R/usr/share/metainfo"
    install -m 644 "$DATA/wps-clip-bridge.metainfo.xml" \
        "$R/usr/share/metainfo/wps-clip-bridge.metainfo.xml"
fi

echo "==> 安装开机自启项"
install -d -m 755 "$R/etc/xdg/autostart"
install -m 644 "$DATA/wps-clip-bridge-autostart.desktop" \
    "$R/etc/xdg/autostart/wps-clip-bridge.desktop"

echo "==> 安装 man page"
install -d -m 755 "$R/usr/share/man/man1"
gzip -9nc "$DATA/wps-copy.1" > "$R/usr/share/man/man1/wps-copy.1.gz"
chmod 644 "$R/usr/share/man/man1/wps-copy.1.gz"

echo "==> 安装文档"
install -d -m 755 "$R/usr/share/doc/$PKG"
install -m 644 "$DEB/copyright" "$R/usr/share/doc/$PKG/copyright"
install -m 644 "$HERE/README.md" "$R/usr/share/doc/$PKG/README.md"
gzip -9nc "$DEB/changelog" > "$R/usr/share/doc/$PKG/changelog.gz"
chmod 644 "$R/usr/share/doc/$PKG/changelog.gz"

echo "==> 生成 DEBIAN 控制文件"
install -d -m 755 "$R/DEBIAN"
install -m 644 "$DEB/control" "$R/DEBIAN/control"
install -m 644 "$DEB/conffiles" "$R/DEBIAN/conffiles"
install -m 755 "$DEB/postinst" "$R/DEBIAN/postinst"
install -m 755 "$DEB/prerm" "$R/DEBIAN/prerm"
install -m 755 "$DEB/postrm" "$R/DEBIAN/postrm"

# 清理不应入包的东西
find "$R" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$R" -name '*.pyc' -delete 2>/dev/null || true

# 按实际大小回填 Installed-Size
SIZE=$(du -sk --exclude=DEBIAN "$R" | cut -f1)
sed -i "s/^Installed-Size:.*/Installed-Size: $SIZE/" "$R/DEBIAN/control"
echo "    已安装大小：${SIZE} KB"

echo "==> 打包"
dpkg-deb --build --root-owner-group "$R" "$OUT/$NAME.deb" >/dev/null

echo ""
echo "构建完成：$OUT/$NAME.deb"
ls -lh "$OUT/$NAME.deb" | awk '{print "    体积：" $5}'

if [ "$1" = "--check" ]; then
    echo ""
    echo "==> 检查：包信息"
    dpkg-deb -I "$OUT/$NAME.deb"
    echo "==> 检查：包含的文件"
    dpkg-deb -c "$OUT/$NAME.deb" | awk '{print "    " $1, $6}'
    echo "==> 检查：菜单项语法"
    desktop-file-validate "$DATA/wps-clip-bridge.desktop" && echo "    菜单项 OK"
    desktop-file-validate "$DATA/wps-clip-bridge-autostart.desktop" && echo "    自启项 OK"
fi
