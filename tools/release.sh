#!/bin/sh
# 一条命令发版：构建 → 算校验和 → 生成发布说明 → 打 tag 推送 → gh release create
#
#   ./tools/release.sh                 按 debian/control 里的版本号发版
#   ./tools/release.sh --draft         先建成草稿，网页上确认后再点 Publish
#   ./tools/release.sh --title "..."   自定义 Release 标题（默认用 tag 名）
#   ./tools/release.sh --dry-run       只做检查、构建、生成说明，不推送也不建 Release
#   ./tools/release.sh --no-push       不碰远端 git，只在本机打 tag
#   ./tools/release.sh --allow-dirty   工作区有未提交改动时也继续（默认拦下来）
#
# 前提：
#   1. 先把 debian/control 的 Version 和 debian/changelog 改成新版本
#   2. gh 已登录：gh auth login（只需一次）
#
# 依赖：gh、git、dpkg-deb
set -e

HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT="$HERE/dist"
DEB="$HERE/debian"

DRAFT=0
DRY=0
PUSH=1
ALLOW_DIRTY=0
TITLE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --draft)       DRAFT=1 ;;
        --dry-run)     DRY=1 ;;
        --no-push)     PUSH=0 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        --title)       shift; TITLE="$1" ;;
        -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "未知参数：$1（-h 看用法）" >&2; exit 2 ;;
    esac
    shift
done

die() { echo "发版中止：$*" >&2; exit 1; }

# gh 经常装在 ~/.local/bin，某些非登录 shell（cron、IDE 内嵌终端）拿不到这个路径，
# 所以自己找一遍，不依赖 PATH
GH=$(command -v gh 2>/dev/null || true)
if [ -z "$GH" ] && [ -x "${HOME:-/root}/.local/bin/gh" ]; then
    GH="$HOME/.local/bin/gh"
fi

VERSION=$(sed -n 's/^Version:[[:space:]]*//p' "$DEB/control" | head -1)
[ -n "$VERSION" ] || die "debian/control 里没读到 Version"
TAG="v$VERSION"
DEBNAME="wps-clip-bridge_${VERSION}_all.deb"
NOTES="$OUT/RELEASE-$VERSION.md"
[ -n "$TITLE" ] || TITLE="$TAG"

cd "$HERE"

echo "==> 版本 $VERSION（tag $TAG）"

# ---- 前置检查 ----------------------------------------------------------
command -v git >/dev/null 2>&1 || die "缺少 git"

if [ -n "$(git status --porcelain)" ] && [ "$ALLOW_DIRTY" -eq 0 ]; then
    echo "    工作区有未提交的改动：" >&2
    git status --short >&2
    die "先提交，或加 --allow-dirty 强行继续"
fi

# changelog 第一段必须是当前版本，否则发出去的说明对不上版本
CHANGELOG_TOP=$(sed -n '1s/.*(\([^)]*\)).*/\1/p' "$DEB/changelog")
[ "$CHANGELOG_TOP" = "$VERSION" ] || \
    die "debian/changelog 顶部是 $CHANGELOG_TOP，和 control 里的 $VERSION 不一致"

if [ "$DRY" -eq 0 ]; then
    [ -n "$GH" ] || die "没装 gh（见 README「发版」一节）"
    "$GH" auth status >/dev/null 2>&1 || die "gh 还没登录，先跑：$GH auth login"
    REPO=$("$GH" repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || \
        die "认不出这个仓库，检查 git remote 是否指向 GitHub"
    echo "    仓库：$REPO"
fi

# ---- 构建 --------------------------------------------------------------
echo "==> 构建安装包"
# 旧产物先清掉，免得 release 里混进上一版的 deb
find "$OUT" -maxdepth 1 -name 'wps-clip-bridge_*_all.deb' -delete 2>/dev/null || true
./build.sh >/dev/null || die "构建失败，单独跑 ./build.sh 看详情"
[ -f "$OUT/$DEBNAME" ] || die "构建完了却没找到 $OUT/$DEBNAME"
(cd "$OUT" && sha256sum "$DEBNAME" > SHA256SUMS)
echo "    $(cat "$OUT/SHA256SUMS")"

# ---- 发布说明 ----------------------------------------------------------
# 生成逻辑在 tools/changelog-notes.py，CI 的发版流程用同一份，
# 免得「本地发的说明」和「CI 发的说明」慢慢长得不一样。
echo "==> 生成发布说明"
"$HERE/tools/changelog-notes.py" "$VERSION" -o "$NOTES" --deb "$DEBNAME" \
    || die "生成发布说明失败"
echo "    $NOTES（$(wc -l < "$NOTES") 行）"

if [ "$DRY" -eq 1 ]; then
    echo ""
    echo "==> --dry-run：到此为止，未推送、未建 Release"
    head -5 "$NOTES"
    exit 0
fi

# ---- git tag 与推送 ----------------------------------------------------
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "==> tag $TAG 已存在，复用"
else
    echo "==> 打 tag $TAG"
    git tag -a "$TAG" -m "$TAG"
fi

if [ "$PUSH" -eq 1 ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    echo "==> 推送 $BRANCH 与 $TAG"
    git push origin "$BRANCH" || die "推送分支失败"
    git push origin "$TAG" || die "推送 tag 失败"
fi

# ---- 建 Release --------------------------------------------------------
ARGS=""
[ "$DRAFT" -eq 1 ] && ARGS="--draft"

if "$GH" release view "$TAG" >/dev/null 2>&1; then
    echo "==> Release $TAG 已存在，更新说明与附件"
    "$GH" release edit "$TAG" --title "$TITLE" --notes-file "$NOTES"
    "$GH" release upload "$TAG" "$OUT/$DEBNAME" "$OUT/SHA256SUMS" --clobber
else
    echo "==> 创建 Release $TAG"
    "$GH" release create "$TAG" \
        --title "$TITLE" \
        --notes-file "$NOTES" \
        $ARGS \
        "$OUT/$DEBNAME" "$OUT/SHA256SUMS"
fi

echo ""
"$GH" release view "$TAG" --json url,isDraft -q '"完成：" + .url + (if .isDraft then "（草稿，需到网页点 Publish）" else "" end)'
