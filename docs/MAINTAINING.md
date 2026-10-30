# 维护者手册

## 构建

```sh
./build.sh              # 输出到本项目 dist/
./build.sh --check      # 额外打印包内容并校验 desktop 文件语法
OUT=/tmp/out ./build.sh # 换个输出目录
```

只依赖 `dpkg-deb` 和 `gzip`，不需要 `debhelper` / `fakeroot`（用 `--root-owner-group`）。

构建完再看一眼包里的东西对不对：

```sh
./tools/check-deb.sh                                  # 检查 control 里那个版本的包
./tools/check-deb.sh dist/wps-clip-bridge_1.7.2_all.deb
```

不带参数时按 `debian/control` 的版本号去找对应的包 —— `dist/` 里留着历史版本，
随便抓一个会把旧包的版本号跟 control 比，报「不一致」。一次只能检查一个包。

它不装包、也不解包到磁盘，直接打开 `.deb` 读里面的文件做断言：菜单项的 `Name`、
`Icon` 和实际图标文件对不对得上、`Exec` 指向的二进制在不在包里、控制脚本有没有
执行位、还有没有旧名字残留。这些漏了在本地看不出问题，装到别人机器上才暴露。
CI 每次构建都会跑这一关。

界面行为没有单测能覆盖（需要显示器），靠探针：

```sh
mkdir -p /tmp/.X11-unix && Xvfb :99 -screen 0 1280x820x24 &
DISPLAY=:99 python3 tools/probe-gui.py     # 全部通过时退出码 0
```

它在一个假 HOME 里造 260 个文档，真开窗口去断言：超量时只画 200 条且状态栏
写明还剩多少、搜索能命中第 200 条之后的文件、拖列宽会写进配置并在重开窗口后
恢复、托盘的点击回调真能把单个路径交给复制流程并给出反馈。截图落在
`build/probe-gui.png`。

这套探针抓过两个单测抓不到的 bug：`CellRendererText.markup` 是只写属性
（`get_property` 会抛），以及窗口尺寸变化引起的列宽重算会被误当成用户拖动。

## 发版

**推荐走 CI：推一个 tag 就发完**，不依赖谁的本机环境装了什么。CI 跑在
x86_64 上没关系 —— 包是 `Architecture: all`，纯 Python + 数据文件，和架构无关。

```sh
# 1. 改三处版本号（三者必须一致，workflow 会核对）
#    src/wpsclip/core.py 的 VERSION
#    debian/control 的 Version
#    debian/changelog 顶部加一段（结尾 " -- 名字 <邮箱>  日期" 那行不能省）

git commit -am "release: 1.7.3"
git tag v1.7.3
git push origin master --tags
```

tag 推上去后 `.github/workflows/release.yml` 依次做：

1. 核对 tag、`control`、`changelog` 三处版本一致 —— 不一致直接失败，不会发出
   文件名和标题对不上的包；
2. 跑一遍全部单元测试（tag 可能打在没走过 CI 的提交上，这是最后一道闸）；
3. `./build.sh` → `./tools/check-deb.sh`；
4. 用 `tools/changelog-notes.py` 从 changelog 生成发布说明，算 `SHA256SUMS`；
5. `gh release create`，带上 `.deb`、`SHA256SUMS` 和说明；
6. 另起一个 job 把同一个 deb 塞进 `gh-pages` 上的 apt 源，用户 `apt upgrade` 就能
   拿到（见下面「apt 源」一节）。

Release 已存在时走「更新说明 + 覆盖附件」，所以同一个 tag 重推、或手动
`workflow_dispatch` 重跑都不会报错。

注意 `workflow_dispatch` 重跑时 **checkout 的是那个 tag，不是 master**：想改
Release 标题或说明就得先改 `debian/changelog`、重新打 tag（删了再打），否则
重跑出来还是旧文案。只改标题的话也可以直接
`gh release edit v1.11.0 --title "..."`，但下次从这个 tag 重跑会被覆盖回去。

> **一次性设置**：仓库的 Settings → Actions → General → Workflow permissions
> 要允许 *Read and write permissions*，否则第 5 步会报
> `Resource not accessible by integration`。workflow 里已经声明了
> `permissions: contents: write`，但仓库级开关关着也照样没权限。

**本地一键发版**（CI 用不了、或者想先看看构建结果再决定）：

```sh
./tools/release.sh --dry-run                   # 只构建 + 生成说明，不推送不发版
./tools/release.sh                             # 构建 + 校验和 + 说明 + 推 tag + 建 Release
./tools/release.sh --title "v1.7.2 · 一句话"    # 自定义 Release 标题
./tools/release.sh --draft                     # 先建草稿，网页上确认后再 Publish
```

它做的事和 CI 那套一样，说明生成用的是同一个脚本（`tools/changelog-notes.py`），
所以两条路发出来的说明不会长得不一样。Release 已存在时改走
`gh release edit` + `gh release upload --clobber`，重跑安全。

两条路可以混用：本地发过之后 CI 再跑同一个 tag，附件会被覆盖成 CI 构建的那份。

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

**完全手工**（没 gh、没网络、CI 坏掉时）：

1. 改版本号（同上）。
2. `./build.sh --check` → 产出 `dist/wps-clip-bridge_<版本>_all.deb`；
   `(cd dist && sha256sum wps-clip-bridge_<版本>_all.deb > SHA256SUMS)`。
3. `git commit` → `git tag v<版本>` → `git push origin master --tags`。
4. 打开 <https://github.com/mzjw159/wps-clip-bridge/releases/new>，tag 选**已有**的
   `v<版本>`（别在网页上新建 tag，否则会和本地那个分叉），说明粘
   `tools/changelog-notes.py <版本>` 的输出，附件传 `.deb` 和 `SHA256SUMS`，然后 Publish。

第 4 步是独立的：`git push --tags` 只推 tag 这个引用，**Release 是 GitHub 上另一个
对象**（带说明和附件），只能用网页或 GitHub API 建，`git push` 变不出来。

## apt 源（GitHub Pages）

用户加一次源，以后 `sudo apt upgrade` 就能升级，不用每次来 Release 页下载。
源托管在同一个仓库的 `gh-pages` 分支上，由 GitHub Pages 发布。

### 结构

```
gh-pages 分支根目录
├── index.html                          跳转到 apt/
├── .nojekyll                           别让 Pages 走 Jekyll
└── apt/
    ├── index.html                      给用户看的说明（含加源命令）
    ├── pool/main/w/wps-clip-bridge/    所有历史 deb，一个都不删（可降级）
    └── dists/stable/
        ├── Release                     Packages 的大小与 MD5/SHA256
        └── main/binary-all/Packages{,.gz}
```

访问地址：<https://mzjw159.github.io/wps-clip-bridge/apt/>

### 加包 / 重新生成

```sh
tools/build-apt-repo.py <站点根目录> dist/wps-clip-bridge_1.8.0_all.deb
```

它会把 deb 拷进 `pool/`，然后**全量重扫 pool** 重新生成 `Packages`、`Packages.gz`
和 `Release`。所以：

- 新版本追加进去，老版本自动保留；
- 想撤掉某个版本，从 `pool/` 里删掉文件再跑一次（不给参数也行，它只看目录）；
- `Packages.gz` 用 `mtime=0` 压的，内容没变时字节完全一样，不会产生无意义的提交。

日常发版不用手动跑：`.github/workflows/release.yml` 里的 `apt` job 会在 Release
建好之后自动下载那个 deb、更新 gh-pages 并推送。

### Pages 设置（只需一次）

Settings → Pages → Source 选 `Deploy from a branch`，分支 `gh-pages`、目录 `/`。
仓库里已经配好了，改分支名的话记得同步这里。

也可以用 API 查：

```sh
gh api repos/mzjw159/wps-clip-bridge/pages | jq .source
```

### 为什么不签名

签名要维护一把长期 GPG 密钥，密钥丢了或泄了比不签更麻烦，密钥轮换还得让所有
用户重新导入。所以这里是**未签名源**，用户那一行必须带 `[trusted=yes]`：

```
deb [trusted=yes] https://mzjw159.github.io/wps-clip-bridge/apt stable main
```

别在文档里省掉 `[trusted=yes]` —— 省掉了 `apt update` 会直接报
`NO_PUBKEY`，用户会以为源坏了。

### 排错

| 现象 | 原因 |
|---|---|
| `apt update` 报 `Hash Sum mismatch` | `Release` 与 `Packages` 不是同一次生成的，重跑一次脚本 |
| `apt update` 报 `404` | gh-pages 还没推上去，或 Pages 构建还没完成（看 Actions 的 pages build） |
| `E: 找不到软件包` | `dists/stable/...` 路径或组件名（`main`）与 sources.list 里不一致 |
| 装了旧版 | 同一个包在 pool 里有多份时，apt 取版本号最大的；确认新 deb 真的进了 pool |

自查（不装包就能确认仓库自洽）：

```sh
python3 -m unittest tests.test_apt_repo      # 结构、字段、哈希、字节稳定性
```

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
