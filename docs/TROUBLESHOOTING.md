# 排障

先跑自检，它把数据源、依赖、会话类型、后台进程一次看全：

```sh
wps-copy --doctor
wps-copy --status
```

日志在 `~/.local/state/wps-clip-bridge/`，每个组件一份：

```sh
tail -n 40 ~/.local/state/wps-clip-bridge/daemon.log
tail -n 40 ~/.local/state/wps-clip-bridge/bridge.log
```

`bridge.log` 里这几行有用：

| 日志 | 含义 |
|---|---|
| `published copy 1 path(s): [...]` | 成功把文件放进剪贴板 |
| `REQ FILE-URI <- text/uri-list (N bytes)` | 有应用来取文件了 |
| `owner=0x... targets=[...]` | 剪贴板换了持有者，以及它声明了哪些格式 |
| `file-copy detected but no path parsed` | 认出了是文件复制但解析不出路径 |

## 粘贴出来还是灰的

先确认：**复制之后有没有再碰过 WPS 的「复制」按钮**。那个按钮是空操作，一点就把
剪贴板清空，覆盖掉刚放进的文件。

## Wayland 应用里粘不出文件（GTK4 / Qt6 程序）

X11 的剪贴板和 Wayland 的剪贴板是两套东西，中间由 XWayland 桥接。桥接对
`text/uri-list`（"这是一批文件"）并不可靠 —— 于是在 GTK4 原生、Qt6 wayland
这类程序里按 `Ctrl+V` 什么都不出来。

装 `wl-clipboard` 就好：

```sh
sudo apt install wl-clipboard
wps-copy --doctor | grep wl-copy     # 应该显示 ✔ 已安装
```

装好之后每次复制会额外用 `wl-copy` 写一份到原生 Wayland 剪贴板，X11 那边照旧。
`wl-copy` 自己不报错但也没生效的话，看日志：

```sh
tail -20 ~/.local/state/wps-clip-bridge/wl-copy.log
```

最常见的是合成器不支持 `wlr-data-control`（少数平铺窗管），那种情况下只能退回去
用 XWayland 程序粘贴。

## 列表是空的

```sh
python3 -m wpsclip.recent      # 直接看解析结果（PYTHONPATH=src）
```

一条都没有 → 看 `--doctor` 的三层命中数，多半是 WPS 升级改了格式，见
[DATA-SOURCE.md](DATA-SOURCE.md)。

## 文件明明在，列表里却没有 / 少了一半

先看窗口顶部的两个下拉：`全部类型`、`全部来源`。选了「表格」就只剩表格，
选了「微信」就只剩微信目录里的 —— 两个是叠加的。筛掉之后中间的提示会写
「筛选（表格 / 微信）下没有文档」，状态栏也会带上「筛选：…」。

调回「全部类型」「全部来源」即可，或者直接点下拉右边的 `×`，一下清掉两个。

**筛选会记住**（1.10.0 起）：下次打开窗口还是上次那个视角，所以「只看到几条」
往往不是文件丢了。改回全部后这个选择会立刻写回配置；也可以手改
`~/.config/wps-clip-bridge/config.ini` 的 `filter_type` / `filter_source`（清空即全部）。

命令行不受这个影响：`wps-copy --list` 和 `wps-copy <序号>` 始终是完整清单。

## 只看到前 200 个，更早的文件找不到

列表一次最多画 200 条（再多滚动条也翻不动）。超出时状态栏会写
「另有 N 个更早的未列出」，鼠标停上去能看到说明。

**搜索不受这个上限影响**：直接搜文件名，筛选与搜索都跑在全部文档上。
命令行也不受限制：`wps-copy --list 关键词`。

## 点托盘菜单里的「最近文档」没反应（1.10.0 及更早）

1.10.0 及更早的版本有这个问题：点托盘右键菜单里列出的那 5 个文档，
既没复制也没提示。原因是托盘把「单个路径」传给了只吃列表的函数，字符串被
按字符拆开，一个都不是文件，函数直接静默返回。1.11.0 已修，复制成功会弹通知。

如果 1.11.0 之后仍然点不动，按顺序看：

1. 右键托盘图标 →「显示窗口」，看列表里那个文件还在不在（可能已经被删了，
   这时会弹「没有可复制的文件」）。
2. 看 `~/.local/state/wps-clip-bridge/gui.log` 里有没有 `从托盘复制失败`。
3. 托盘菜单里那几行是窗口刷新列表时生成的 —— 一直没开过窗口就用的是启动
   那一刻的列表，点开窗口刷新一下再试。

## 列表顺序乱了 / 想看最大的文件

点表头的「名称 / 来源 / 类型 / 大小 / 修改时间 / 所在文件夹」即可排序：
一下升序、再一下降序、第三下回到按时间分组（表头上的 `↑` `↓` 是当前方向）。
手动排序时不按「今天 / 昨天」分组 —— 两套顺序会打架。

排序**不记忆**，关掉窗口再开还是按修改时间分组。命令行对应
`wps-copy --list --sort size --reverse`。

## 列宽想恢复默认 / 换了显示器后列太宽

拖过表头分隔条之后宽度会写进 `config.ini` 的 `col_widths`，下次打开照旧。
只有拖过的列才记；「所在文件夹」列永远不记，它是自动吃掉剩余宽度的那一列。

要恢复默认：把 `col_widths = {}` 写回去，或在表头分隔条上双击让 GTK 自己
量一次（双击之后再拖一下就会记成新值）。

## 托盘图标是系统默认齿轮

面板没认到图标。先清缓存：

```sh
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor
rm -rf ~/.cache/ukui ~/.cache/icon-cache.kcache
```

托盘和任务栏里的旧图标会一直用到进程重启 —— 从托盘菜单「退出」再打开。
原因见 [DESIGN.md](DESIGN.md#图标为什么要装两份)。

## 托盘点了不出窗口

收进托盘的（只是隐藏）和缩在任务栏的（被窗管最小化）走两套逻辑，后者在 KWin 系上
有个异步坑。现在两条路都有重试和 EWMH 激活请求兜底；如果仍然点不出来，把
`~/.local/state/wps-clip-bridge/gui.log` 里带 `提升窗口失败` / `X11 激活窗口失败`
的行贴出来。

## 热键没反应

- `Ctrl+Alt+C` 可能被别的程序占用：抢不到时会弹一条通知并写进日志（X 的 GrabKey
  冲突只会异步返回 BadAccess，v1.3.0 之前是完全静默的）。设置里换一个组合键。
- 全局热键只在 X11 / XWayland 应用前台时有效，见 README「已知限制」。

## 改了源码但像没生效

`service.exe_for()` 优先使用 `/usr/bin/` 下已安装的入口，找不到才退回 `python -m`。
也就是说系统里装过旧 deb 时，你改的 `bridge.py` / `hotkey.py` 不会自动生效：

```sh
wps-copy --stop                                  # 先停掉已安装的后台
PYTHONPATH=$PWD/src python3 -m wpsclip.bridge    # 前台跑改动过的服务
```

或者改完直接重装：`sudo apt install --reinstall ./dist/wps-clip-bridge_*.deb`。
