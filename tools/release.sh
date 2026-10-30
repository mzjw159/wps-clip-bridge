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
    command -v gh >/dev/null 2>&1 || die "没装 gh（见 README「发版」一节）"
    gh auth status >/dev/null 2>&1 || die "gh 还没登录，先跑：gh auth login"
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || \
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
echo "==> 生成发布说明"
VERSION="$VERSION" NOTES="$NOTES" DEBNAME="$DEBNAME" TAG="$TAG" \
/usr/bin/env python3 - <<'PY' || die "生成发布说明失败"
import io, os, re

ver = os.environ['VERSION']
raw = io.open('debian/changelog', encoding='utf-8').read()
parts = re.split(r'(?m)^wps-clip-bridge \((\S+)\)', raw)
sec = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
if ver not in sec:
    raise SystemExit('changelog 里没有 %s' % ver)

lines = [l.strip() for l in sec[ver].splitlines()
         if l.strip() and not l.startswith(' -- ')]
lines = lines[1:]                        # 去掉 "unstable; urgency=medium" 那行

out = ['## 本次更新', '']
for l in lines:
    if l.startswith('* '):
        out.append('- ' + l[2:])
    else:
        out.append('  ' + l)             # changelog 里的续行保持缩进，读着像段落
out += ['', '## 安装', '', '```bash', 'sudo dpkg -i ' + os.environ['DEBNAME'], '```', '',
        '卸载：`sudo dpkg -r wps-clip-bridge`', '',
        '## 校验', '', '```bash', 'sha256sum -c SHA256SUMS', '```', '']
io.open(os.environ['NOTES'], 'w', encoding='utf-8').write('\n'.join(out))
PY
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

if gh release view "$TAG" >/dev/null 2>&1; then
    echo "==> Release $TAG 已存在，更新说明与附件"
    gh release edit "$TAG" --title "$TITLE" --notes-file "$NOTES"
    gh release upload "$TAG" "$OUT/$DEBNAME" "$OUT/SHA256SUMS" --clobber
else
    echo "==> 创建 Release $TAG"
    gh release create "$TAG" \
        --title "$TITLE" \
        --notes-file "$NOTES" \
        $ARGS \
        "$OUT/$DEBNAME" "$OUT/SHA256SUMS"
fi

echo ""
gh release view "$TAG" --json url,isDraft -q '"完成：" + .url + (if .isDraft then "（草稿，需到网页点 Publish）" else "" end)'
