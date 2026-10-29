# wps-clip-bridge

让 WPS for Linux 里的文档能正常复制、粘贴到桌面和微信。

---

## 它解决什么问题

在 Windows 上，WPS 文档列表里右键「复制」，然后去桌面或微信按 `Ctrl+V`，文件就过去了。
在银河麒麟（UKUI / Wayland 会话）上，同一个按钮点下去，到哪儿粘贴都是灰的。

原因不是配置问题，是 WPS 那个按钮在 Linux 版里**没有实现**：

```
CLIPBOARD 持有者：Qt Selection Owner for wpsoffice
对外声明的格式：  TARGETS / MULTIPLE / TIMESTAMP / SAVE_TARGETS
逐格式盲请求 12 种（text/uri-list、x-special/*、UTF8_STRING…）：全部为空
扫描 X 服务器 1~14000 号原子，找 WPS 私有通道：无
```

也就是说它只「占住」了剪贴板，一个字节的文件数据都没写进去，也没有藏到别的地方。
数据从未离开 WPS 进程，外部做格式转换、做中转都无从下手 —— 这是死路。

所以本工具**绕开那个按钮**：直接读取 WPS 自己的最近文档记录，把文件作为真正的
文件对象放进剪贴板。桌面、微信、邮件客户端都能直接粘贴。

（顺带它也修另一类问题：某些文件管理器/代理复制文件时漏写 `text/uri-list`，
或者把 `copy` 头行写进了 `text/uri-list`，导致桌面端解析失败、粘贴变灰。）

---

## 安装

从 [Releases](https://github.com/mzjw159/wps-clip-bridge/releases) 下载最新的
`wps-clip-bridge_x.y.z_all.deb`（同时提供 `SHA256SUMS` 校验），然后三种方式任选：

```sh
sudo apt install ./dist/wps-clip-bridge_1.5.0_all.deb   # 命令行
```

- **双击** .deb 文件，用系统的安装器（麒麟上是「安装器 / kylin-installer」）；
- 或者命令行 `sudo apt install ./wps-clip-bridge_1.4.1_all.deb`；
- 或者离线时 `sudo dpkg -i wps-clip-bridge_1.4.1_all.deb`。

从 1.4.1 起这三种方式**都不需要额外装任何东西**：`python-xlib` 已经打包在
程序内部（系统里装了 `python3-xlib` 时优先用系统的），剩下的依赖
`python3-gi` / `gir1.2-gtk-3.0` 是任何 GTK 桌面本来就会有的。
`dpkg -i` 也不用再补依赖。

装完 **不需要重启电脑**，执行一次即可开始使用：

```sh
wps-copy --start
```

（后台服务同时设为登录自启，以后开机会自动运行。）

### 如果你是之前手工部署过的

先跑一次迁移，清掉旧的自启项和残留进程，避免两套程序抢剪贴板和热键：

```sh
wps-copy --migrate
```

---

## 使用

三个入口，按习惯挑一个。

### 1. 全局热键（最快）

按 `Ctrl+Alt+C` → 弹出最近文档列表 → **双击**要的那一行 → 切到桌面或微信按 `Ctrl+V`。

窗口卡在后台时再按一次热键，会把已有窗口提到前面，不会重复开。

另一组热键 `Ctrl+Shift+C` 更省事：**不弹窗**，直接把最近一个文档放进剪贴板，
适合「就复制刚才那个」的场景。复制完会弹一条桌面通知告诉你复制的是哪个文件 ——
这条路径没有窗口也没有状态栏，通知就是你唯一的反馈。

> `Ctrl+Shift+C` 在终端、VS Code 这类程序里常被用作「复制」，全局热键会优先
> 截获它（终端里按了没反应就是这个原因）。介意的话在设置里换成
> `Ctrl+Shift+Alt+C` 之类。两组键都能改。

### 2. 图形窗口

应用菜单 →「WPS 最近文档复制」，或：

```sh
wps-copy --gui
```

窗口里有搜索框、刷新、以及 **「浏览本地文件…」**（支持多选）——
列表里没有的文件（比如从没在 WPS 里打开过的）可以直接选。

**一次发多个文件**，有两种方式：

- **多选再复制**：`Ctrl` 点选、`Shift` 连选（或 `Ctrl+A` 全选），
  然后按 `Ctrl+C`、双击其中任意一行、或点「复制 N 个文件」。
- **直接拖出去**：把选中的行拖到微信聊天窗口、桌面或文件管理器，
  松手即发送/复制，省掉「复制 → 切窗口 → 粘贴」三步。
  拖拽会在光标下显示文件类型图标，落点应用收到的是标准的 `text/uri-list`。

分组标题（今天 / 昨天 / …）只是分隔，不参与选中，`Ctrl+A` 也不会把它们算进去。

**复制是在后台线程里做的**，点下去界面不会僵住：状态栏左边会转一个小进度圈，
按钮暂时变成「正在复制…」，期间你照样能搜索、改选、折叠分组。连点几次也没问题
——只有最后一次的结果会显示出来，慢的那次不会回来把状态盖掉。

标题栏右上角的 **齿轮按钮** 打开设置窗口（详见下面「设置」一节）。

### 3. 命令行

```sh
wps-copy                # 复制最近修改的第 1 个
wps-copy --list         # 看清单和序号
wps-copy 3              # 复制第 3 个
wps-copy 报告            # 复制文件名含「报告」的
wps-copy -f a.pdf b.doc # 直接指定文件，可多个
```

其它：

```sh
wps-copy --start        # 启动后台服务
wps-copy --stop         # 停止后台服务
wps-copy --status       # 看服务 / 热键 / 窗口的状态
wps-copy --doctor       # 自检：数据源命中数、依赖、会话类型
wps-copy --migrate      # 清理旧的手工部署
```

### 关于云文档

WPS 云文档在本地通常留有一份缓存副本，这些也会出现在列表里，类型列标记为「云」。
纯在线的、本地没有任何落盘文件的文档无法复制 —— 没有文件对象就没有可粘贴的东西。

---

## 数据从哪来：WPS 升级了怎么办

Office.conf 是 **WPS 内部私有格式，不是公开接口**。它哪天把 `RecentFiles` 改个名、
把转义换个写法，只认键名的解析就会静默返回空列表 —— 这是这类工具的先天风险。
所以最近文档分三层取，任何一层有收获就用：

| 层 | 做法 | 抗什么 |
|---|---|---|
| 1. 结构化 | 按键名 `wps\RecentFiles\files\N\path=` 解析 | 正常情况，能拿到序号 |
| 2. 通用扫描 | 同一个文件里，凡是「值像被转义的绝对路径、且指向真实文档」的键值对全部收下 | **键名/层级被改名** |
| 3. 兜底源 | 主源一条都读不出时，改用 GTK 的 `recently-used.xbel` 和 `~/文档/WPS Cloud Files` 里最近改过的文档 | 配置彻底读不出来 |

配置文件本身也有兜底：`~/.config/Kingsoft/Office.conf`、`~/.config/kingsoft/Office.conf`、
`~/.config/Kingsoft/office.conf` 都试。

**最坏情况**（WPS 把值改成加密/base64，连路径形态都没有）三层都无能为力，
但这时不会静默：状态栏会说「WPS 的配置里没解析出任何文档路径 —— 多半是 WPS
升级后改了格式」，并把 tooltip 里的明细和 `wps-copy --doctor` 指给你。

判断当前机器到底走的哪一层：

```sh
wps-copy --doctor
```

它会打印会话类型（X11 / Wayland）、依赖、配置文件的命中数
（`结构化 35 条 / 通用扫描 35 条`）、兜底源是否存在、三个后台进程的状态。
把这个输出发出来，基本能定位所有「列表是空的」问题。

---

## 把安装包发给别人

可以。**直接双击就行**，不用教对方敲命令。

包是 `Architecture: all` 的纯 Python，amd64 / arm64 通用。唯一两个「可能
没有」的依赖 `python3-gi`、`gir1.2-gtk-3.0` 是任何图形桌面自带的东西，
`python-xlib` 已经打进包里了 —— 所以无论双击、`apt install` 还是离线
`dpkg -i`，都不会出现「装完打不开」。

对方机器上需要满足：

- 装的是 **WPS for Linux**（`~/.config/Kingsoft/Office.conf` 存在），且至少打开过一个文档；
- 桌面是 X11 或 Wayland（Wayland 下通过 XWayland 工作，见「已知限制」）；
- 有 `~/.config` 写权限（普通用户都有）。

装好后让对方跑一次 `wps-copy --doctor`，把输出对一下「解析」那几行的条数，
就知道他那边是不是和你的效果一致。

---

## 设置

窗口标题栏右上角的齿轮按钮，或托盘菜单里的「设置…」。

| 分组 | 项目 | 说明 |
|---|---|---|
| 全局快捷键 | 唤起窗口 / 复制最近文档 | 点按钮后**直接按下组合键**即可录入，不需要记格式；`Delete` 清空，`Esc` 取消。至少带一个修饰键 |
| 外观 | 主题 | 跟随系统 / 浅色 / 深色，改完立即生效 |
| 外观 | 列表最多显示 | 20–500 条 |
| 窗口与托盘 | 关闭时收进托盘 | 点 ✕ 或按 `Esc` 只隐藏，不退出程序 |
| 窗口与托盘 | 复制后自动收起 | 复制成功就把窗口收起来，不打断你粘贴 |
| 窗口与托盘 | 启动时待在托盘 | 开机/启动时不弹窗，只在托盘待命 |
| 窗口与托盘 | 复制成功发通知 | 走 `notify-send`；窗口收起时、以及用快捷键复制时，这是唯一的结果反馈 |
| 窗口与托盘 | 托盘图标样式 | 自动 / 跟随面板 / 经典图标。经典图标可以「点一下即显示窗口」 |
| 启动 | 开机自动启动 | 写 `~/.config/autostart/` 的屏蔽项，不动 `/etc` 下的系统文件 |

托盘图标：**双击或中键点它唤回窗口**；菜单里还有「显示窗口」「复制最近文档」
「设置…」「退出」。

双击这件事受面板限制：StatusNotifier 协议里，面板对左键的处理固定是弹菜单，
程序改不了。程序能做的是把「次级激活」（中键、以及不少面板转发给它的双击）
指向「显示窗口」。想要严格意义上的「单击即显示窗口」，把上面的「托盘图标样式」
改成「经典图标」——它走老的 XEmbed 托盘，点击语义由程序自己决定。

如果桌面环境既不支持 StatusNotifier 也不支持 XEmbed 托盘，程序会自动退化为
「关闭即退出」，避免窗口消失后无处唤回。

---

## 配置文件

上面的设置都会写进 `~/.config/wps-clip-bridge/config.ini`，也可以手动改：

```ini
[hotkey]
enabled = true
combo = <Control><Alt>c          ; 唤起窗口；也接受 Ctrl+Alt+C 这种写法
copy_enabled = true
copy_combo = <Control><Alt>v     ; 直接复制最近文档

[bridge]
poll = 0.3                       ; 剪贴板轮询间隔（秒）

[ui]
limit = 80                       ; 列表最多显示多少条
theme = system                   ; system / light / dark
close_to_tray = true             ; 关闭时收进托盘
hide_after_copy = true           ; 复制成功后自动收起窗口
start_hidden = false             ; 启动时待在托盘
notify_on_copy = true            ; 复制成功发通知
tray_backend = auto              ; auto / indicator / statusicon
```

手工改完热键后，在设置窗口里点一次保存，或 `wps-copy --stop && wps-copy --start`，
让热键进程重新抓取组合键。

---

## 卸载

```sh
sudo apt remove wps-clip-bridge
```

`/etc/xdg/autostart` 里的自启项、`/usr/share` 下的文件随包一起删除。
你改过的配置和日志不会自动删，需要彻底清理就手动删这两个目录：

```sh
rm -rf ~/.config/wps-clip-bridge ~/.local/state/wps-clip-bridge
```

---

## 从源码构建

```sh
cd ~/coding/wps-clip-bridge
./build.sh              # 输出到本项目 dist/
./build.sh --check      # 额外打印包内容并校验 desktop 文件语法

OUT=/tmp/out ./build.sh # 换个输出目录
```

只依赖 `dpkg-deb` 和 `gzip`，不需要 `debhelper` / `fakeroot`（用 `--root-owner-group`）。

### 换图标

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
...
```

**83% 是怎么来的。** SVG 的 viewBox 是 `0 0 1024 1024`。图形如果铺满整个
viewBox（最初那版就是），渲染出来的图标在面板和任务栏里会比邻居大一圈 ——
workbuddy 这类系统图标的内容只占画布 `40/48 ≈ 83%`。所以现在 SVG 最外层套了：

```xml
<g transform="translate(87.04,87.04) scale(0.83)">
```

换新图后先跑一次 `./tools/render-icons.py --check`（只看不改）判断：

- 占比已经在 83% 附近 → 不用动 SVG，直接渲染；
- 明显偏大或偏小 → 调 `scale()` 与 `translate()`，公式是
  `translate = 1024 × (1 − scale) / 2`。

**为什么同一套图要装两份。** 名字分别是 `wps-file` 和 `wps-clip-bridge`。
后者是给麒麟安装器和软件中心用的 —— 它们按**包名**去图标主题里查
（`QIcon::fromTheme('wps-clip-bridge')`），只装前者就显示成系统默认的齿轮。
`build.sh` 会自动装两份，换图标时不用管。

**换完没生效？** 是缓存：

```sh
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor
rm -rf ~/.cache/ukui ~/.cache/icon-cache.kcache     # 面板自己的图标缓存
```

另外，托盘和任务栏里的旧图标会一直用到进程重启 —— 从托盘菜单「退出」再打开。

---

## 开发

### 不装包，直接从源码跑

所有模块都能 `-m` 运行，把 `src/` 加进 `PYTHONPATH` 即可：

```sh
cd ~/coding/wps-clip-bridge
export PYTHONPATH=$PWD/src

python3 -m wpsclip.recent        # 只做一件事：打印解析出的最近文档列表
python3 -m wpsclip.cli --list    # 命令行清单
python3 -m wpsclip.gui           # 图形窗口
python3 -m wpsclip.bridge        # 剪贴板服务（前台跑，日志直接看）
python3 -m wpsclip.daemon        # 看护进程（依次拉起 bridge + hotkey）
```

依赖：`python3-gi` + GTK3（图形窗口）、`python3-xlib`（X11 selection，已随包内置一份在 `src/wpsclip/_vendor/`，系统装了就用系统的）。
代码兼容 Python 3.8+（麒麟 V10 自带 3.8）。

### 跑测试

纯函数部分有单元测试（`tests/`，150 个用例），不需要图形界面，也不碰你的
真实配置（每个用例都在临时目录里跑）：

```sh
python3 -m unittest discover -s tests          # 全跑，约 1 秒
python3 -m unittest discover -s tests -v       # 逐个用例
python3 -m unittest tests.test_service -v      # 只跑复制编排
python3 -m unittest tests.test_recent -v       # 只跑解析与多源兜底
python3 -m unittest tests.test_hotkey -v       # 只跑热键反馈
python3 -m unittest tests.test_vendor -v       # 只跑内置依赖自检
```

覆盖 `unesc()` 的「2~4 位十六进制」边界、`parse_uris()` 的百分号解码、
`bucket_of()` 的时间分桶、配置的 mtime 热重载、开机自启开关，以及复制链路
的路径整理、请求发布、等待确认和异步回调（不需要 X 连接）。

> 动 `recent.py` 的解析规则之前先读 `tests/test_recent.py`：
> 里面每条用例都对应一个真实踩过的坑，改坏了会立刻红。

### 一个必须知道的开发坑

`src/wpsclip/service.py` 的 `exe_for()` **优先使用 `/usr/bin/` 下已安装的入口**，
找不到才退回 `python -m`。也就是说：

> 只要系统里装过 1.0.0 的 deb，源码树里改的 `cli` / `gui` 会生效（由你直接执行），
> 但 `ensure_bridge()` 拉起的**后台服务仍然是 `/usr/bin/wps-clip-bridge`**（安装的那份）。

所以改 `bridge.py` / `hotkey.py` / `daemon.py` 时要二选一：

```sh
wps-copy --stop                                  # 先停掉已安装的后台
PYTHONPATH=$PWD/src python3 -m wpsclip.bridge    # 前台跑改动过的服务
```

或者改完直接重装：`sudo apt install --reinstall ./dist/wps-clip-bridge_*.deb`。

### 关键调试入口

```sh
python3 -m wpsclip.recent      # 确认 Office.conf 解析对不对（最常见的出错点）

PYTHONPATH=src python3 -c "from wpsclip.core import clipboard_has; print(clipboard_has('/任意/存在的/文件'))"
```

`recent.py` 里那个 `\x` 变长转义正则（`{1,4}`、遇非十六进制即停）是全项目最容易踩坑的
一行，动它之前先看下面「为什么读 `Office.conf`」那节。

---

## 目录结构

源码树：

```
wps-clip-bridge/
├── src/wpsclip/             Python 包
│   ├── core.py              路径、配置、日志、单实例锁、剪贴板读取
│   ├── bootstrap.py         入口脚本的友好启动（缺依赖给人话）
│   ├── recent.py            最近文档：三层解析 + 兜底源 + 自检诊断
│   ├── _vendor/             内置的 python-xlib 与 six（系统没装时兜底）
│   ├── bridge.py            常驻剪贴板服务
│   ├── service.py           服务编排：确保在跑、异步复制、确认结果
│   ├── cli.py               命令行 wps-copy
│   ├── gui.py               图形窗口 wps-copy-gui
│   ├── hotkey.py            全局热键 wps-copy-hotkey
│   └── daemon.py            看护进程 wps-clip-bridge-daemon
├── bin/                     5 个可执行入口（薄壳，转手给上面的模块）
├── data/                    菜单项、自启项、图标、man page
├── tools/                   辅助脚本（render-icons.py：从 SVG 出各尺寸 PNG）
├── tests/                   单元测试（不需要图形环境）
├── debian/                  control、维护脚本、copyright、changelog
├── dist/                    打包产物（已放 1.3.0）
├── build/                   构建中间目录，每次构建重建，可随时删
├── build.sh
└── .gitignore
```

安装后：

```
/usr/bin/wps-copy                    命令行
/usr/bin/wps-copy-gui                图形窗口
/usr/bin/wps-copy-hotkey             热键守护
/usr/bin/wps-clip-bridge             剪贴板服务
/usr/bin/wps-clip-bridge-daemon      后台守护（自启的就是它）
/usr/lib/python3/dist-packages/wpsclip/
/usr/share/applications/wps-clip-bridge.desktop
/usr/share/icons/hicolor/{48x48,64x64,128x128,256x256,512x512,scalable}/apps/wps-file.*
/usr/share/icons/hicolor/{...}/apps/wps-clip-bridge.*   同上图标，按包名的别名
/usr/share/pixmaps/wps-file.{png,svg}                   只认 pixmaps 的老面板
/usr/share/metainfo/wps-clip-bridge.metainfo.xml        AppStream 元数据
/usr/share/man/man1/wps-copy.1.gz
/etc/xdg/autostart/wps-clip-bridge.desktop
```

图标为什么装两份、还放 pixmaps：不同组件查图标的方式不一样 —— 桌面环境按
`.desktop` 的 `Icon=wps-file` 查主题、麒麟安装器/软件中心按**包名**
（`wps-clip-bridge`）查 `QIcon::fromTheme()`、个别老面板只扫 `/usr/share/pixmaps`。
同一套图标按这几个名字/目录各放一份，才能都命中，否则会显示成系统通用图标。

运行期文件走 XDG，不写死家目录：

| 用途 | 位置 |
|---|---|
| 锁文件、复制请求 | `$XDG_RUNTIME_DIR/wps-clip-bridge/`（注销自动清理） |
| 日志 | `~/.local/state/wps-clip-bridge/`（daemon / bridge / hotkey / gui 各一份） |
| 配置 | `~/.config/wps-clip-bridge/config.ini` |

---

## 几个设计上的取舍

**为什么必须有一个常驻进程？**

X11 的 selection（剪贴板）由**写入方**持有，写入方退出，剪贴板内容就没了。
所以工具窗口不能自己当持有者，否则窗口一关就复制失效。
常驻的 `bridge` 负责长期持有，窗口只负责「告诉它复制哪个」。

**为什么用 flock 而不是 `pgrep -f`？**

`pgrep -f` 会匹配到命令行里恰好含同样字样的其他进程 —— 开发过程中就踩过：
热键进程把当前 shell 的命令行当成了「GUI 已在运行」，于是永远不弹窗。
flock 是内核级互斥，不会误判。

**为什么有 `daemon` 这一层？**

登录自启不提供崩溃重启，所以 `daemon` 负责看护 `bridge` 和 `hotkey`，
子进程异常退出就重启，连续崩溃则指数退避（2s → 30s），免得刷日志烧 CPU。
它还负责在启动前等 X 就绪 —— 登录自启可能比合成器更早执行。

**为什么复制要放到后台线程？**

复制不是一个纯本地动作：它要先确认剪贴板服务在跑（必要时拉起守护进程，
冷启动可能要一两秒），再写请求文件、等 `published` 时间戳变化。整条链路
最长要几秒，而 GTK 主循环被占住时窗口连重绘都做不了——表现就是「点一下
界面灰掉、鼠标转圈」。

所以 `service.request_copy()` 只负责**同步**语义（命令行、热键进程直接用它，
阻塞一下无所谓），界面走 `service.copy_async()`：工作线程里跑完整条链路，
结果用 `GLib.idle_add` 回到主线程再碰控件。界面侧再用一个自增序号作废旧
回调——连续点两次时，先发后到的结果会被丢掉，状态栏只反映最后一次。

**为什么读 `Office.conf` 而不是调 WPS 的接口？**

WPS 没有提供可用的对外接口。`~/.config/Kingsoft/Office.conf` 是它自己的最近文档
记录，格式是一个 INI，路径用 `\x` + **2~4 位**十六进制转义。

这里有个坑值得记下来：ASCII 字符也会被转义，比如文件名里的 `2026` 写成
`\x32\x30\x32\x36`。早期按「定长 4 位」解析会把 `\x32\x30` 吃成一个字符 `0x3230`，
整条路径变乱码、文件被判为不存在而被过滤掉 —— 表现就是「名字带数字的文件在列表里
看不到」。现在按「最多 4 位、遇非十六进制字符即停」解析。

---

## 已知限制

- **热键只在 X11 / XWayland 应用前台时有效。** WPS、微信、文件管理器都是 XWayland
  客户端，所以日常够用；但如果当前焦点在 Wayland 原生应用（如某些 GTK4 程序）上，
  X11 的 `grab_key` 收不到按键。这是 X11 键盘抓取的固有限制，无解。
- **`Ctrl+Alt+C` 可能被其他程序占用。** 抢不到时会弹一条通知并写进日志，
  在设置里换一个组合键即可（X 的 GrabKey 冲突只会异步返回 BadAccess，
  v1.3.0 之前是完全静默的）。
- **只列本机存在文件。** 纯云端、本地无落盘文件的文档没有可复制的对象。
- **禁用了 `universe` 源也没关系。** `python3-xlib` 已内置在包里
  （v1.4.1 起），不需要从源里装。

---

## 排障

```sh
wps-copy --doctor                     # 先跑这个：数据源/依赖/会话/服务一次看全
wps-copy --status                     # 三个组件是否都在跑
tail -n 40 ~/.local/state/wps-clip-bridge/daemon.log
tail -n 40 ~/.local/state/wps-clip-bridge/bridge.log
```

`bridge.log` 里这几行有用：

- `published copy 1 path(s): [...]` —— 成功把文件放进剪贴板
- `REQ FILE-URI <- text/uri-list (N bytes)` —— 有应用来取文件了
- `owner=0x... targets=[...]` —— 剪贴板换了持有者，以及它声明了哪些格式
- `file-copy detected but no path parsed` —— 认出了是文件复制但解析不出路径

如果粘贴还是灰的，先确认：**复制之后有没有再碰过 WPS 的「复制」按钮**。
那个按钮是空操作，一点就把剪贴板清空，覆盖掉刚放进的文件。
