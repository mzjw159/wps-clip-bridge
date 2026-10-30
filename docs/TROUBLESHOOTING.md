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

## 列表是空的

```sh
python3 -m wpsclip.recent      # 直接看解析结果（PYTHONPATH=src）
```

一条都没有 → 看 `--doctor` 的三层命中数，多半是 WPS 升级改了格式，见
[DATA-SOURCE.md](DATA-SOURCE.md)。

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
