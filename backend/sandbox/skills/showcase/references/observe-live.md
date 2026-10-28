# 观察运行中的页面（≤60 行）

`cheese check` 渲染的是你手里的 HTML 文件；要看**活着的页面**（正在跑的前端、
登录后才看得到的界面）用 `scripts/shot_live.py` —— 它驱动机器上的浏览器截一张
真图给你看。

## 什么时候用

- 改前端之前：先截一张「现在的样子」，量出要改的规格（字号、行距、间距）。
- 改完之后：同一 URL、同一宽度再截一张，和改前那张逐项对。
- 用户说「这个页面看起来不对」而你手头只有代码：先看真图再下判断。

## 用法（两个版本，先试 Node）

**先试 Node 版**：复用工作目录附近已经装好的 playwright，零下载、秒起。

```bash
node scripts/shot_live.mjs <url> <out.png> --user <账号> --password <密码>
```

- 它从「当前目录 + 往上几层 + 各层的 `e2e/`」里找 `node_modules` 里的 playwright
  （cheese 仓库的 `e2e/node_modules` 就命中）。找不到时用
  `CHEESE_PW_ROOT=<含 node_modules 的目录>` 指给它。
- 所以要**在装着 playwright 的那个工作目录里**跑（cheese 仓库根目录即可）。

跑不动（那台机器上没装 playwright）再退 Python 版，它自带依赖但要下一次：

```bash
uv run --with playwright python scripts/shot_live.py <url> <out.png> \
  --user <账号> --password <密码>
```

**国内直连 PyPI 的 wheel 常常卡死**（实测 0 B/s），而那正是 Python 版第一次要下的
东西——卡住就换 Node 版，别干等。本机已在 `~/.config/uv/uv.toml` 把默认索引指到
清华镜像，但老版本 uv 未必读到。

两版参数一样：

- **默认弹出真实浏览器窗口**（有头、每步慢放 250ms、截完停 2.5 秒再关）——因为
  这个脚本是**给人看着跑的**：改界面时，人要看着它真在点那个页面。要它安静跑就加
  `--headless`。
- 公开页面不用登录，省掉 `--user/--password`。
- 已有 token 就 `--token <tok>`，跳过登录。
- `--click "按钮文字"`：截图前点一颗控件（弹窗、页签这类要点才出来的界面用它）。
- `--width 400` 看手机宽；`--wait` 默认 3500ms，SPA 转久就加大。
- 截图是 png，用 Read 打开看。最终 URL 会打印出来：被弹回登录页就是
  token 没生效或过期，重新登录再截。

## 与 `cheese check` 的分工

| 要看什么 | 用什么 |
|---|---|
| 你刚写的、要摆出去的 HTML 文件 | `cheese check <路径>`（平台浏览器，双宽 + 撑破报告） |
| 正在跑的服务、别人的页面、登录后的界面 | `scripts/shot_live.py`（本机浏览器，带登录态） |

## 纪律

- 浏览器用系统 Chrome；没有才用 playwright 自带 chromium（首次自动装一次）。
  不为看图装第二个浏览器，不写新的截图脚本——这个脚本就是那一个。
- 截图当证据，不当装饰：改前改后两张要同 URL、同宽度、同主题，差才能算数。
