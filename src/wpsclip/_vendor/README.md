# 内置的第三方依赖

这里的代码**不是本项目写的**，原样复制自发行版提供的 Python 包，目的是让
wps-clip-bridge 在「没装 python3-xlib」的机器上也能直接运行 —— 别人双击
安装包装完就能用，不必再去软件源拉依赖。

| 目录 / 文件 | 上游 | 版本 | 许可 |
| --- | --- | --- | --- |
| `Xlib/` | python-xlib | 0.23（`python3-xlib 0.23-2build1`） | GPL-2+ |
| `six.py` | six | 1.14.0（`python3-six 1.14.0-2`） | MIT |

`six` 不是可有可无的：python-xlib 0.23 在 `display.py`、`protocol/rq.py` 等
六处 `from six import ...`，只带 Xlib 会在导入时直接 ImportError。

`wpsclip/__init__.py` 把本目录**追加**到 `sys.path` 末尾，于是：

- 系统装了 `python3-xlib` / `python3-six` → 用系统的那份（可能有发行版补丁）；
- 没装 → 用这里的副本。

`wps-copy --doctor` 会显示当前用的是哪一份（`Xlib 0.23（内置）` / `（系统）`）。

## 升级这份副本

```sh
cp -r /usr/lib/python3/dist-packages/Xlib src/wpsclip/_vendor/
cp /usr/lib/python3/dist-packages/six.py src/wpsclip/_vendor/
find src/wpsclip/_vendor -name '__pycache__' -type d -exec rm -rf {} +
python3 -m unittest tests.test_vendor -v
```

## 许可

python-xlib 的上游元数据（PKG-INFO）标的是 `LGPLv2+`，但它随附的版权文件
正文是 **GPL-2+**。按较严格者声明，详见 `debian/copyright` 里
`Files: src/wpsclip/_vendor/Xlib/*` 一段。`six` 为 MIT。

出现问题时先看这两条：`Xlib` 目录被误删 → 打包照常成功但运行即报
ImportError；`import` 顺序被改 → 本机（装过 python3-xlib）一切正常，
换台机器就暴露。`tests/test_vendor.py` 专门守这两种情况。
