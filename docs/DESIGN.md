# 设计取舍

这份文档记录「为什么是现在这个架构」，以及几个踩过坑之后才定下来的做法。
只想知道怎么用的，看 [README](../README.md) 就够了。

## 为什么要绕开 WPS 的「复制」按钮

WPS for Linux 的文档列表里右键「复制」，在桌面或微信里粘贴出来是灰的。查下来不是
配置问题，是那个按钮**没有实现**：

```
CLIPBOARD 持有者：Qt Selection Owner for wpsoffice
对外声明的格式：  TARGETS / MULTIPLE / TIMESTAMP / SAVE_TARGETS
逐格式盲请求 12 种（text/uri-list、x-special/*、UTF8_STRING…）：全部为空
扫描 X 服务器 1~14000 号原子，找 WPS 私有通道：无
```

也就是说它只「占住」了剪贴板，一个字节的文件数据都没写进去，也没有藏到别的地方。
数据从未离开 WPS 进程，外部做格式转换、做中转都无从下手 —— 这是死路。

所以本工具绕开那个按钮：直接读 WPS 自己的最近文档记录，把文件作为真正的文件对象
（`text/uri-list`）放进剪贴板。桌面、微信、邮件客户端都能直接粘贴。

顺带它也修另一类问题：某些文件管理器/代理复制文件时漏写 `text/uri-list`，或者把
`copy` 头行写进了 `text/uri-list`，导致桌面端解析失败、粘贴变灰。

## 为什么必须有一个常驻进程

X11 的 selection（剪贴板）由**写入方**持有，写入方退出，剪贴板内容就没了。所以工具
窗口不能自己当持有者，否则窗口一关就复制失效。

常驻的 `bridge` 负责长期持有，窗口只负责「告诉它复制哪个」。

## 为什么用 flock 而不是 `pgrep -f`

`pgrep -f` 会匹配到命令行里恰好含同样字样的其他进程。开发过程中就踩过：热键进程把
当前 shell 的命令行当成了「GUI 已在运行」，于是永远不弹窗。flock 是内核级互斥，
不会误判。

## 为什么有 daemon 这一层

登录自启不提供崩溃重启，所以 `daemon` 负责看护 `bridge` 和 `hotkey`：子进程异常
退出就重启，连续崩溃则指数退避（2s → 30s），免得刷日志烧 CPU。它还负责在启动前等
X 就绪 —— 登录自启可能比合成器更早执行。

## 为什么复制要放到后台线程

复制不是纯本地动作：先要确认剪贴板服务在跑（必要时拉起守护进程，冷启动可能要一两
秒），再写请求文件、等 `published` 时间戳变化。整条链路最长要几秒，而 GTK 主循环
被占住时窗口连重绘都做不了 —— 表现就是「点一下界面灰掉、鼠标转圈」。

所以 `service.request_copy()` 只负责**同步**语义（命令行、热键进程直接用它，阻塞
一下无所谓），界面走 `service.copy_async()`：工作线程里跑完整条链路，结果用
`GLib.idle_add` 回到主线程再碰控件。界面侧再用一个自增序号作废旧回调 —— 连续点
两次时，先发后到的结果会被丢掉，状态栏只反映最后一次。

## 窗口为什么自己实现边缘缩放

麒麟的 UKUI（KWin 系）下有两层坑：

- 用 GTK 的 CSD（`Gtk.HeaderBar`）时窗口没有边框，拖边缘只会**移动**窗口；
- 即使退回 SSD 原生标题栏，`_NET_FRAME_EXTENTS` 报的是 `0,0,38,0` —— 左右下三边
  宽度为 0，抓不住；`Gdk.Window.begin_resize_drag()` 发出 `_NET_WM_MOVERESIZE`
  后 KWin 收下请求但不执行。

最后是**内侧热区**：Grid 里铺 3×3 的 5px `Gtk.EventBox`，按键时靠 X 的隐式指针抓取
收 motion/release，自己算 `x/y/w/h` 再 `move() + resize()`。

一个容易再次踩到的细节：`set_visible_window(False)` 会让 `get_has_window()` 变成
False，事件一个都收不到；要保留窗口，再把背景设成全透明。

## 窗口最小化后为什么唤不出来（以及怎么修的）

`deiconify()` 只是给窗管发了个**异步**请求。窗口真正取消图标化之前紧跟着的
`present()`，会被窗管当成「对还在任务栏里的窗口」的无效请求丢掉；KWin 系还会以
「防抢焦点」为由干脆不理 —— 于是点了像没点。

现在最小化时按拍重试（70ms 一拍、最多 8 拍），每拍补一条 EWMH
`_NET_ACTIVE_WINDOW`（source=2，等同于用户从任务栏点窗口），**每拍先问窗管的
`WM_STATE`，窗口一出来立刻收手**。

「是否还在任务栏里」必须问 ICCCM 的 `WM_STATE`，不能读 `Gdk.Window.get_state()`：
只要自己调过 `deiconify()`，GDK 就把那个标记抹掉了，之后永远返回 False。

还有一层兜底：第 6 拍仍不行就 hide → 刷事件循环 → show_all → 原样还原位置尺寸。
实测这台 KWin 连 MapRequest 也不理（`XMapWindow`、`iconify+deiconify` 抖动、
`hide+show` 单独用全都无效），所以真正管用的是激活请求，重挂只是给别的窗管留后路。

## 托盘为什么自己实现 StatusNotifierItem

libappindicator 有两个硬伤：

- 固定 `ItemIsMenu=true`，左键只能弹菜单，点半天窗口不出来；
- 只把图标**路径**当图标名交给面板，面板按图标主题一查就落空，于是显示成系统默认
  的齿轮图标。

`src/wpsclip/sni.py` 自己实现 `org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`：
`ItemIsMenu=false`，`Activate` 直接显示窗口，`IconName` 和 `IconPixmap`（ARGB32）
两样都给，面板挑哪个都能命中。

后端仍然保留三级回退：原生 SNI → libappindicator → `Gtk.StatusIcon`（XEmbed）。
桌面环境两者都不支持时，程序退化为「关闭即退出」，避免窗口消失后无处唤回。

## 图标为什么要装两份

名字分别是 `wps-file` 和 `wps-clip-bridge`。后者是给麒麟安装器和软件中心用的 ——
它们按**包名**去图标主题里查（`QIcon::fromTheme('wps-clip-bridge')`），只装前者就
显示成系统默认的齿轮图标。`build.sh` 会自动装两份。

## 为什么打包时自带 python-xlib

麒麟默认禁用了 `universe` 源，`python3-xlib` 经常装不上。把 `_vendor/Xlib` 打进
deb（系统里装了 `python3-xlib` 时优先用系统的），双击安装、离线 `dpkg -i` 都不会
出现「装完打不开」。
