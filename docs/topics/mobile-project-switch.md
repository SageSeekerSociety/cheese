# 移动端的三个问题

@符露夀 在这个话题里提了三件事，都在移动端。下面按「是什么 / 查到什么 / 改了什么」记。

## 1. 手机上不能切项目 —— 已修

**症状确认属实**：手机上进了一个项目就出不去，没有任何一处能列出你的其他项目。

查到的原因：
- 桌面端的项目切换器是左侧那条竖 rail（一个项目一格方头像，见 <&frontend/src/components/common/Navigation/destinations.ts> 的 `railItems()`），而它在 <&frontend/src/App.vue> 里包在 `mdAndUp` 里，**手机上整个不渲染**。
- 手机底栏固定三格（空间/工作区/待办），「工作区」只落到**一个**项目：正开着的 → 上次开过的 → 第一个。
- 手机顶栏那格项目名本来就是个菜单，也已经拿到了 `projects`，但菜单里只有「项目设置」。
- 没有「我的项目」列表页兜底（`/projects/:id` 之外没有 `/projects` 索引）。

**改法（@符露夀 拍板选的 A）**：<&frontend/src/components/TopicSidebar.vue> 顶栏那个项目菜单里加一段项目列表，点一个就换到那个项目的地址。只在整页形态（手机）下列——桌面那条竖 rail 已经是这个入口，同一件事两个入口只会让人猜哪个算数。

用例：<&frontend/src/components/TopicSidebar.projectSwitch.spec.ts>（列出来了 / 点了真的换过去 / 桌面不重复 / 只有一个项目时不列），4 条通过。

## 2. 键盘弹起来输入框不上移 —— 已改，等真机复验

仓库里本来就有一套：<&frontend/src/lib/keyboardInset.ts> 把键盘遮住的高度写成 `--keyboard-inset`，<&frontend/src/style.css> 减掉它。查下来有两处会让它失效：

1. **只减了 `min-height`（下限），撑不住比它高的内容**。用真 Chromium 量过：390×780 的屏、键盘 300px，消息区放 2000px 长内容时输入框底跑到 **2120px**（可见区底是 480px）；改成同时写 `height` 之后是 **480px**。
2. **测量基准取错了**。原来拿 `window.innerHeight` 当「整屏高度」，但在一部分手机浏览器上它会跟着键盘一起缩——那时它和 visualViewport 一样高，算出来的遮挡恒为 0，页面一动不动，而这恰恰是最需要这段代码的那些浏览器。改成用布局视口 `document.documentElement.clientHeight`：CSS 里的 `100%` / `100dvh` 量的就是它，我们减的也是它。

另外给 <&frontend/index.html> 的 viewport 加了 `interactive-widget=resizes-content`：Chrome 会直接把布局视口缩掉，根本不用走 JS 那条路；Safari 不认这个键，照旧走上面那套。两条路不会减两次（浏览器缩了布局视口之后算出来就是 0）。

用例：<&frontend/src/lib/keyboardInset.spec.ts>，5 条通过。

> 待复验：这一条没法在沙箱里对着真手机验证，**要 @符露夀 在自己手机上再试一次**。

## 3. 移动端文档溢出 —— 待确认具体症状

还没动手，缺一个关键信息：溢出的是哪一处、什么内容。已看到的可疑点：
- 文档正文的左右内边距是给桌面留的：<&frontend/src/components/panels/PanelDoc.vue> 的 `.doc-page` 是 `padding: 32px 48px 72px`，<&frontend/src/components/DocEditor.vue> 的 `.doc-editor` 还有 `padding-left: 56px`（给 ＋/⠿ 拖拽手柄留的位，手机上没有 hover，这块位是白占的）。390px 宽的屏上光内边距就吃掉 152px。
- 这几个文件里**一条 `@media` 都没有**，和「只在 PC 端修复了」对得上。

等 @符露夀 回一张截图或说清是哪一处再改。

## 状态

- 本地跑过：前端全量单测 871 条全绿、eslint 0 error、stylelint 比基线少 1 条、repo-rules PASS。
- 还没递验收卡（等第 3 条一起）。
