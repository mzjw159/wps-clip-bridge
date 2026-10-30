# 维护者手册

## 构建

```sh
./build.sh              # 输出到本项目 dist/
./build.sh --check      # 额外打印包内容并校验 desktop 文件语法
OUT=/tmp/out ./build.sh # 换个输出目录
```

只依赖 `dpkg-deb` 和 `gzip`，不需要 `debhelper` / `fakeroot`（用 `--root-owner-group`）。

## 发版

**一条命令**：

```sh
# 1. 改版本号：src/wpsclip/core.py 的 VERSION、debian/control 的 Version、
#    debian/changelog 顶部加一段（结尾 " -- 名字 <邮箱>  日期" 那行不能省）
# 2. commit 之后：
./tools/release.sh                             # 构建 + 校验和 + 说明 + 推 tag + 建 Release
./tools/release.sh --title "v1.7.2 · 一句话"    # 自定义 Release 标题
./tools/release.sh --draft                     # 先建草稿，网页上确认后再 Publish
./tools/release.sh --dry-run                   # 只构建 + 生成说明，不推送不发版
```

脚本依次做：核对 `control` 与 `changelog` 版本一致 → `./build.sh` → 重算
`dist/SHA256SUMS` → 从 `debian/changelog` 抽当前版本生成 `dist/RELEASE-<版本>.md`
→ 打 tag 并推送 → `gh release create` 带上 `.deb` 与 `SHA256SUMS`。

Release 已存在时改成 `gh release edit` + `gh release upload --clobber`，所以同一条
命令重跑是安全的。

前置：`gh` 装好并登录过一次。

```sh
gh auth login        # 选 GitHub.com → 浏览器登录，粘一次设备码即可
gh auth status       # 确认已登录
```

`gh` 是单个静态二进制，arm64 版解压即用、不依赖 glibc 版本，放 `~/.local/bin/gh`
即可，不用 sudo 装包：

```sh
curl -L -o gh.tar.gz https://github.com/cli/cli/releases/download/v2.103.3/gh_2.103.3_linux_arm64.tar.gz
tar xzf gh.tar.gz --no-same-owner --no-same-permissions gh_2.103.3_linux_arm64/bin/gh
install -m 755 gh_2.103.3_linux_arm64/bin/gh ~/.local/bin/gh
```

`release.sh` 会自己找 `gh`（PATH 里没有 `~/.local/bin` 也能找到）。
`gh auth login` 若卡在 `Bad Gateway`，是代理的问题不是授权失败 —— 换一个能通
`github.com` 的代理重试即可（`api.github.com` 通不代表登录能成）。

**手工四步**（没有 gh、或想逐步确认时）：

1. 改版本号（同上）。
2. `./build.sh --check` → 产出 `dist/wps-clip-bridge_<版本>_all.deb`；
   `(cd dist && sha256sum wps-clip-bridge_<版本>_all.deb > SHA256SUMS)`。
3. `git commit` → `git tag v<版本>` → `git push origin master --tags`。
4. 打开 <https://github.com/mzjw159/wps-clip-bridge/releases/new>，tag 选**已有**的
   `v<版本>`（别在网页上新建 tag，否则会和本地那个分叉），说明抄 `debian/changelog`
   里那一段，附件传 `.deb` 和 `SHA256SUMS`，然后 Publish。

第 4 步是独立的：`git push --tags` 只推 tag 这个引用，**Release 是 GitHub 上另一个
对象**（带说明和附件），只能用网页或 GitHub API 建，`git push` 变不出来。

## 换图标

图标只有一个源文件：`data/wps-file.svg`。换的时候不用碰别的：

```sh
cp 新图标.svg data/wps-file.svg     # 1) 覆盖，文件名保持不变
./tools/render-icons.py             # 2) 渲染 48/64/128/256/512 + 500px 主图
./build.sh                          # 3) 重新打包
```

脚本会打印每一档的内容占比，正常都在 83% 左右：

```
尺寸     内容占比     文件
48     83%      data/icons/hicolor/48x48/apps/wps-file.png  ✔
64     84%      data/icons/hicolor/64x64/apps/wps-file.png  ✔
```

**83% 是怎么来的。** SVG 的 viewBox 是 `0 0 1024 1024`。图形如果铺满整个 viewBox
（最初那版就是），渲染出来的图标在面板和任务栏里会比邻居大一圈 —— 系统图标的内容
一般只占画布 `40/48 ≈ 83%`。所以现在 SVG 最外层套了：

```xml
<g transform="translate(87.04,87.04) scale(0.83)">
```

换新图后先跑一次 `./tools/render-icons.py --check`（只看不改）判断：

- 占比已经在 83% 附近 → 不用动 SVG，直接渲染；
- 明显偏大或偏小 → 调 `scale()` 与 `translate()`，公式是
  `translate = 1024 × (1 − scale) / 2`。

同一套图会按 `wps-file` 和 `wps-clip-bridge` 两个名字各装一份，原因见
[DESIGN.md](DESIGN.md#图标为什么要装两份)。

## 改 README 里的截图

`docs/screenshot.png` 是在 Xvfb 里跑真实程序截的，数据是虚构的（`/tmp/demo` 下的
空文件），不含任何真实文档名。重截只要把假文档和 `Office.conf` 换掉再跑一遍 GUI
即可，注意用独立的 `HOME` / `XDG_*` 目录，避免读到本机真实记录。
