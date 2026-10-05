# 知是设计系统（页面版）

> **只在没有模板能套时才读这份。** 报告、看板、数据表、对比、计划、说明、幻灯片、一页纸都有成品模板，
> 样式与 token 已经写好在里面（各自的 `references/<type>.md` 会说清怎么用）。这份 750 行的参考是给
> 「八套模板都不合适、要自己设计一页」的情形兜底用的——正常摆一份页面不必读它。

摆出来的每一份 HTML 页面都从这套 token 起步。值不是另起一套，是从产品自己的设计语言
（`frontend/src/style.css` 的 `:root` 与 `[data-theme='dark']`，`docs/design-system.md` 的原则）
折成一份单文件页面能直接粘贴的参考。照它做出来的页面，放进房间预览里，读起来像产品自己的一页。

先定档：报告、看板、方案对比、数据表都用**实用档**——层次清楚、间距讲究、配色正经，配色只服务内容。
表现档（大头图、编过场的动效）留给对外的落地页，本参考不覆盖。

---

## 1. 原则

产品的气质是「温暖的精密」：像一间安静、明亮的工作室，工具摆放整齐，屋子是暖的。落到页面上：

1. **内容是主角，界面退后。** 边框、底色、标签只用最少的量把结构说清楚。省下的空间留白，不拿来塞更多东西。分组用间距，不用线框。
2. **约 95% 是中性色。** 标题 `--cx-ink`、正文 `--cx-text`、次要 `--cx-muted`、元信息 `--cx-faint`。整页的观感由这条灰阶撑起。
3. **琥珀只给一处。** 一页里琥珀只出现在「全页最要紧的那一个元素」或「唯一的主操作」上：一颗主按钮，或一个读数。它靠稀有才有用；拿去装饰，用处就没了。
4. **状态色只用于真实状态。** 通过、警告、失败是三种，不造第四种。三者都用 `x-ink` 写字、`x-wash` 做底、`x`（记号色）画点或边框（见 §4.5）。
5. **清晰优先于巧妙。** 熟悉的排版、直白的文案、一眼能懂的层次。不为新奇牺牲可读。
6. **留白是结构。** 越相关的离得越近：组内 8–12px，组间 24–32px。能用间距分开的，就不加分隔线。
7. **重复元素一致。** 一排卡片、一列标签、兄弟项上的小标签，边缘、基线、内边距用同一套；同一类元素每次在同一个位置。
8. **卡片样式只给需要突出的那一块。** 卡片只描边不投影（`--cx-shadow-1` 只给浮层）。每块都描边等于都没描边。
9. **一打开就完整。** 第一帧静止画面就是缩略图，要读的内容加载完就看得见，不留 `opacity: 0` 等滚动。
10. **每个状态都值得认真做。** 空状态、加载、出错、键盘焦点、深色模式，与正常状态同等重要。

**避开 AI 味。** 不做：米色底配衬线大标题配赤陶色；近黑配一抹荧光绿；报纸式细线加密集分栏；白底上一块紫蓝渐变头图；emoji 当小标题；全部居中；处处大圆角；**圆角卡片左边加一条强调竖条**。本参考里的块靠底纹和间距立起来，不靠左竖条。

---

## 2. Token（可直接粘贴）

页面是**一个自包含的文件**：CSS、JS、图片（data URI）都写进这一个 `.html`，不引 Google Fonts、cdnjs、
unpkg 这类外网资源——国内常常打不开，失败还静默，页面会悄悄变样。确需某个库时，把它的源码内联进来。
`<title>` 和 `<style>` 放在文档最前面（只扫文件开头，也保证第一帧就是完整的样子）。

单文件页面把下面整段放进 `<style>`，放在文档最前面。它自己处理三种主题状态：裸 `:root` 是亮色，
`@media (prefers-color-scheme: dark)` 里写 `:root:not([data-theme="light"])` 让「明确选亮色」能压过
系统的暗色，`:root[data-theme="dark"]` 再写一遍让「明确选暗色」压过系统亮色。没人设属性时，
多数人看到的就是跟随系统那一版。组件一律通过变量上色，绝不在 media 或 `[data-theme]` 块里直接写颜色。

```html
<style>
:root {
  color-scheme: light;

  /* 中性色阶（暖冷灰）：每档按「信息离读者多远」选，不跳档 */
  --cx-ink: #191a1c;      /* 标题 */
  --cx-text: #36383c;     /* 正文 */
  --cx-muted: #5a5e66;    /* 次要：副标题、说明、轴标签、表头 */
  --cx-faint: #747a82;    /* 元信息：时间戳、ID、计数 */

  --cx-line: #ecedef;     /* 发丝分隔线 */
  --cx-line-2: #e2e3e6;   /* 更明显的分隔线，也是选中行底色 */
  --cx-fill: #f4f5f7;     /* 悬停、内嵌区块 */
  --cx-fill-2: #eeeff1;   /* 比 fill 重一档 */

  --cx-canvas: #f7f8fa;   /* 页面底色 */
  --cx-surface: #ffffff;  /* 内容区、卡片 */
  --cx-raised: #ffffff;   /* 浮层：菜单、弹窗、抽屉 */

  /* 琥珀：全页只给一处 */
  --cx-accent: #f57f17;
  --cx-accent-press: #d96e0a;
  --cx-accent-ink: #9a5413;   /* 琥珀色文字（--cx-accent 写字对比度不够） */
  --cx-accent-wash: #fdf1e2;  /* 极浅琥珀底，罕用 */
  --cx-on-accent: #ffffff;    /* 琥珀实心上的字 */

  /* 状态三件套：记号 / 文字 / 底，不可换用 */
  --cx-ok: #1f9d55;     --cx-ok-ink: #12703a;     --cx-ok-wash: #e8f6ee;
  --cx-warn: #e8901c;   --cx-warn-ink: #8f5406;   --cx-warn-wash: #faf0dc;
  --cx-danger: #dc2626; --cx-danger-ink: #b91c1c; --cx-danger-wash: #fdecec;

  /* 图表系列：分类用，最多六条。两条以内用 --cx-text / --cx-muted 加线型；柱/面这类实心标记用 --cx-text / --cx-faint */
  --cx-chart-1: #3d6bb3;
  --cx-chart-2: #2a9d8f;
  --cx-chart-3: #d9822b;
  --cx-chart-4: #8a63c2;
  --cx-chart-5: #c9506f;
  --cx-chart-6: #6b7b8c;

  /* 代码块：两套主题下都反色 */
  --cx-code-bg: #212121;
  --cx-code-ink: #e0e0e0;

  /* 圆角：三档加胶囊 */
  --cx-radius-sm: 6px;     /* 标签、chip、小徽章 */
  --cx-radius-md: 8px;     /* 按钮、输入框、卡片内的小块 */
  --cx-radius-lg: 12px;    /* 卡片、面板、callout、图 */
  --cx-radius-pill: 999px; /* 胶囊、状态标签、圆点 */

  /* 投影：只给浮层，卡片只描边 */
  --cx-shadow-1: 0 1px 2px rgba(25, 26, 28, 0.06), 0 2px 8px rgba(25, 26, 28, 0.06);
  --cx-shadow-2: 0 4px 12px rgba(25, 26, 28, 0.1), 0 12px 32px rgba(25, 26, 28, 0.1);
  --cx-focus-ring: #c2570a; /* 键盘焦点环，别移除 */

  /* 字体：只用系统中文字体栈，不引外部字体（CJK 网络字体每字重好几兆，也连不上） */
  --cx-font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "PingFang SC",
    "HarmonyOS Sans SC", "MiSans", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  --cx-font-display: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "PingFang SC",
    "HarmonyOS Sans SC", "MiSans", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  /* 前四个 Windows 上没有，落到裸 monospace 会被映射成宋体；垫上自带的 Cascadia Mono / Consolas */
  --cx-font-mono: ui-monospace, SFMono-Regular, Menlo, "Cascadia Mono", Consolas,
    "Liberation Mono", monospace;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;

    --cx-ink: #f3f4f6;
    --cx-text: #d3d6db;
    --cx-muted: #aeb4bd;
    --cx-faint: #888ea0;

    --cx-line: #2b2e33;
    --cx-line-2: #3a3e45;
    --cx-fill: #212429;
    --cx-fill-2: #282c31;

    --cx-canvas: #141517;   /* 不用纯黑：纯黑之下没有更暗的一档，卡片浮不起来 */
    --cx-surface: #1b1d20;
    --cx-raised: #212429;   /* 比 surface 亮一档，浮层靠亮度而非投影分开 */

    --cx-accent: #ffa733;   /* 深色下必须提亮，否则按钮上的字读不出 */
    --cx-accent-press: #ffbc5e; /* 深色下按下变亮，不像亮色那样变深 */
    --cx-accent-ink: #ffc670;
    --cx-accent-wash: #2e2216;
    --cx-on-accent: #191a1c; /* 浅琥珀上的字要用深色 */

    --cx-ok: #3fbf7f;     --cx-ok-ink: #6fd9a0;     --cx-ok-wash: #14261d;
    --cx-warn: #f0a94a;   --cx-warn-ink: #f7c078;   --cx-warn-wash: #2a2114;
    --cx-danger: #f0625c; --cx-danger-ink: #f79490; --cx-danger-wash: #2e1a1a;

    --cx-chart-1: #7aa2e3;
    --cx-chart-2: #4fb8aa;
    --cx-chart-3: #eba25a;
    --cx-chart-4: #ad8fe2;
    --cx-chart-5: #e57f99;
    --cx-chart-6: #9eacba;

    --cx-code-bg: #282c31;  /* 反色块在深色下改用 fill-2，否则和卡片糊在一起 */
    --cx-code-ink: #d3d6db;

    --cx-shadow-1: 0 1px 2px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.3);
    --cx-shadow-2: 0 4px 12px rgba(0, 0, 0, 0.5), 0 12px 32px rgba(0, 0, 0, 0.4);
    --cx-focus-ring: rgba(255, 167, 51, 0.65);
  }
}

:root[data-theme="dark"] {
  color-scheme: dark;

  --cx-ink: #f3f4f6;
  --cx-text: #d3d6db;
  --cx-muted: #aeb4bd;
  --cx-faint: #888ea0;

  --cx-line: #2b2e33;
  --cx-line-2: #3a3e45;
  --cx-fill: #212429;
  --cx-fill-2: #282c31;

  --cx-canvas: #141517;
  --cx-surface: #1b1d20;
  --cx-raised: #212429;

  --cx-accent: #ffa733;
  --cx-accent-press: #ffbc5e;
  --cx-accent-ink: #ffc670;
  --cx-accent-wash: #2e2216;
  --cx-on-accent: #191a1c;

  --cx-ok: #3fbf7f;     --cx-ok-ink: #6fd9a0;     --cx-ok-wash: #14261d;
  --cx-warn: #f0a94a;   --cx-warn-ink: #f7c078;   --cx-warn-wash: #2a2114;
  --cx-danger: #f0625c; --cx-danger-ink: #f79490; --cx-danger-wash: #2e1a1a;

  --cx-chart-1: #7aa2e3;
  --cx-chart-2: #4fb8aa;
  --cx-chart-3: #eba25a;
  --cx-chart-4: #ad8fe2;
  --cx-chart-5: #e57f99;
  --cx-chart-6: #9eacba;

  --cx-code-bg: #282c31;
  --cx-code-ink: #d3d6db;

  --cx-shadow-1: 0 1px 2px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.3);
  --cx-shadow-2: 0 4px 12px rgba(0, 0, 0, 0.5), 0 12px 32px rgba(0, 0, 0, 0.4);
  --cx-focus-ring: rgba(255, 167, 51, 0.65);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--cx-canvas); /* body 必须显式设背景，透明会露出宿主底色还不报警 */
  color: var(--cx-text);
  font-family: var(--cx-font-sans);
  font-size: 16px;
  line-height: 1.75;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

:focus-visible { outline: 2px solid var(--cx-focus-ring); outline-offset: 2px; border-radius: var(--cx-radius-sm); }

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
</style>
```

**名字怎么用。** 选哪一档看信息的重要程度，不看好不好看：标题 `--cx-ink`，正文 `--cx-text`，副标题与说明
`--cx-muted`，时间戳和计数 `--cx-faint`。不要跳档——正文用 `--cx-faint` 不是低调，是让人看不清。
面按层次选：内容区 `--cx-surface`，页面底 `--cx-canvas`，沉下去一块用 `--cx-fill`，浮起来的用 `--cx-raised`。
永远写变量名，不写死色值：写死的 `#36383c` 到深色模式就是黑底黑字。

---

## 3. 版式

### 3.1 字号阶梯（CJK 调过）

中文按每行 35–40 个汉字排版。下面是页面用的档位，守住它，别临时加一档：

| 角色 | 字号 / 行高 | 字重 | 用在哪 |
|---|---|---|---|
| 页面标题 | 34 / 41px（1.2） | 650 | masthead 的 `h1` |
| 分区标题 | 22 / 30px（1.35） | 650 | 一级小节 `h2` |
| 小标题 | 18 / 27px（1.5） | 600 | 二级小节、卡片标题 `h3` |
| 正文 | 16 / 28px（1.75） | 400 | 连续正文 |
| 密正文 | 15 / 26px（1.72） | 400 | lede、callout、表格说明 |
| 元信息 / 表头 | 13 / 20px（1.5） | 400–600 | 眉标、图注、表头、脚注 |
| 读数 | 22 / 28px | 650 | stat tile 的数值 |
| 大读数 | 44 / 1.05 | 650 | readout 那一个大数字 |

行高成对给：`34/41`、`28/37`、`22/30`、`18/27`、`16/28`、`15/26`、`13/20`。数字一律
`font-variant-numeric: tabular-nums`，好纵向比较。

### 3.2 阅读宽度

| 宽度 | 值 | 用在哪 |
|---|---|---|
| 正文 | 660px | 连续正文、报告、memo（默认） |
| 看板 | 1100px | 多列仪表盘、宽表、图表 |
| 全文 | 100% | 数据表浏览页 |

正文栏超过 660px，一行排到六十多个汉字，回行时眼睛找不到下一行的开头。看板页才放宽到 1100px。
两档都 `margin-inline: auto` 居中，左右各留**至少 16px** 边距（本参考用 20px）。

### 3.3 间距

只用 **4 / 8 / 12 / 16 / 24 / 32 / 48px**，用 flex/grid 的 `gap` 给，不靠每个元素各自加 `margin`。
选哪一档看两样东西之间的关系，不看「这里空不空」：

| 关系 | 间距 |
|---|---|
| 同一个组件内部（图标与文字） | 4 / 8 |
| 相关的元素之间（同一张卡片里的几行） | 12 / 16 |
| 组与组、区块与区块之间 | 24 / 32 |
| 页面上的大区块之间 | 48 |

组内间距必须明显小于组间间距，否则分组看不出来。`body` 或外层 wrapper 用 `padding-inline` 给左右边，
`padding-block` 给竖向——**不要用会把左右清零的 `padding` 简写**。

### 3.4 层级与圆角

卡片**只描边不投影**：`background: var(--cx-surface); border: 1px solid var(--cx-line);`。
投影只给浮在页面之上的菜单、弹窗、抽屉（`--cx-shadow-1` / `--cx-shadow-2`）。
圆角三档：小块 `--cx-radius-sm`，按钮与内嵌块 `--cx-radius-md`，卡片与面板 `--cx-radius-lg`，状态标签 `--cx-radius-pill`。

---

## 4. 组件

下面每段都是可直接粘贴的 CSS + HTML。示例内容用真实数字和词组，不要 lorem、不要「示例文字」。

### 4.1 页面骨架

```html
<main class="page">
  <header class="masthead">
    <p class="eyebrow">运营周报 · 2026 年 10 月 4 日</p>
    <h1 class="title">本周任务与运行情况</h1>
    <p class="subtitle">四周内完成任务 491 个，运行时长下降 12%。</p>
  </header>
  <!-- 后面的小节 -->
  <footer class="footnote"><p>数据来源：知是任务库，2026-10-04 快照。</p></footer>
</main>
```

```css
.page {
  max-width: 660px;              /* 看板页用 .page--wide 改 1100 */
  margin-inline: auto;
  padding-block: 48px 64px;      /* 竖向 */
  padding-inline: 20px;          /* 左右至少 16px；不用会清零左右的 padding 简写 */
  display: flex;
  flex-direction: column;
  gap: 40px;                     /* 区块之间统一 40（32–48 之间） */
}
.page--wide { max-width: 1100px; }

/* masthead：眉标 / 标题 / 副标题，一条底线收住 */
.masthead {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--cx-line);
}
.eyebrow { margin: 0; font-size: 13px; line-height: 20px; color: var(--cx-muted); }
.title { margin: 0; font-size: 34px; line-height: 1.2; font-weight: 650; color: var(--cx-ink); text-wrap: balance; }
.subtitle { margin: 0; font-size: 18px; line-height: 27px; color: var(--cx-muted); }

/* 小节：标题 + 内容用 gap 排，间距不靠 margin */
.section { display: flex; flex-direction: column; gap: 12px; }
.section > h2 { margin: 0; font-size: 22px; line-height: 30px; font-weight: 650; color: var(--cx-ink); text-wrap: balance; }
.section > h3 { margin: 0; font-size: 18px; line-height: 27px; font-weight: 600; color: var(--cx-ink); }

.footnote { border-top: 1px solid var(--cx-line); padding-top: 16px; }
.footnote p { margin: 0; font-size: 13px; line-height: 20px; color: var(--cx-muted); }
```

中文字体不要 `text-transform: uppercase`，也不要给汉字加 `letter-spacing`（只有纯拉丁字母数字的小标签可以加一点点）。

### 4.2 lede / 摘要块

一段话把结论先给出来。靠**底纹 + 描边**立起来，**不用「圆角卡片左边加一条强调竖条」那一版**（AI 味清单里）。

```html
<div class="lede">
  <p class="lede__label">摘要</p>
  <p>四周内完成任务 491 个，比上一周期多 23 个；运行总时长下降 12%，在 W40 首次低于 300 小时。</p>
</div>
```

```css
.lede {
  display: flex; flex-direction: column; gap: 6px;
  padding: 16px 20px;
  background: var(--cx-fill);
  border: 1px solid var(--cx-line);
  border-radius: var(--cx-radius-lg);
}
.lede__label { margin: 0; font-size: 13px; line-height: 20px; font-weight: 600; color: var(--cx-muted); }
.lede p { margin: 0; font-size: 15px; line-height: 26px; color: var(--cx-text); }
```

### 4.3 readout（全页那一个大数字）

一页里只有一处这样的读数。数字用 `--cx-accent-ink`，是这一页唯一的琥珀——它就是要让人一眼记住的那一个。
周围留白要足，别和别的元素挤。

```html
<div class="readout">
  <p class="readout__label">本周完成任务</p>
  <p class="readout__value">128</p>
  <p class="readout__note">比上周多 7 个，连续第三周上升。</p>
</div>
```

```css
.readout { display: flex; flex-direction: column; gap: 2px; }
.readout__label { margin: 0; font-size: 13px; line-height: 20px; font-weight: 600; color: var(--cx-muted); }
.readout__value {
  margin: 0; font-size: 44px; line-height: 1.05; font-weight: 650;
  color: var(--cx-accent-ink); font-variant-numeric: tabular-nums;
}
.readout__note { margin: 0; font-size: 13px; line-height: 20px; color: var(--cx-muted); }
```

> 如果一个页面的重点不止一个数，就用下面的 stat tiles，别并排放两个 readout——那样两个都不重要了。

### 4.4 stat tiles（一排小结读数）

一排放得下的几个数，用描边小格。用 `auto-fit` + `minmax`，窄屏自然折成一列，不用写断点。

```html
<div class="stat-row">
  <div class="stat"><p class="stat__label">运行中</p><p class="stat__value">6</p></div>
  <div class="stat"><p class="stat__label">待验收</p><p class="stat__value">14</p></div>
  <div class="stat"><p class="stat__label">本周新增文档</p><p class="stat__value">37</p></div>
  <div class="stat"><p class="stat__label">平均时长</p><p class="stat__value">18<span class="stat__unit">分钟</span></p></div>
</div>
```

```css
.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.stat {
  display: flex; flex-direction: column; gap: 4px;
  padding: 14px 16px;
  background: var(--cx-surface);
  border: 1px solid var(--cx-line);
  border-radius: var(--cx-radius-md);
}
.stat__label { margin: 0; font-size: 13px; line-height: 20px; color: var(--cx-muted); }
.stat__value { margin: 0; font-size: 22px; line-height: 28px; font-weight: 650; color: var(--cx-ink); font-variant-numeric: tabular-nums; }
.stat__unit { margin-left: 4px; font-size: 13px; font-weight: 400; color: var(--cx-muted); }
```

一排小格只在「这几个数就是这一页的重点」时才开场就用；它们是背景，不是主角。

### 4.5 状态标签（pill）

`x-ink` 写字、`x-wash` 做底、`x` 画圆点。三者不可互换：`--cx-warn` 单独在白底上约 2.3:1，写字读不出来。

```html
<span class="pill pill--ok"><span class="pill__dot"></span>通过</span>
<span class="pill pill--warn"><span class="pill__dot"></span>待验收</span>
<span class="pill pill--danger"><span class="pill__dot"></span>失败</span>
<span class="pill pill--neutral"><span class="pill__dot"></span>已归档</span>
```

```css
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 10px;
  font-size: 13px; line-height: 20px;
  border-radius: var(--cx-radius-pill);
  white-space: nowrap;
}
.pill__dot { width: 6px; height: 6px; border-radius: var(--cx-radius-pill); flex: none; }
.pill--ok      { color: var(--cx-ok-ink);     background: var(--cx-ok-wash); }
.pill--ok      .pill__dot { background: var(--cx-ok); }
.pill--warn    { color: var(--cx-warn-ink);   background: var(--cx-warn-wash); }
.pill--warn    .pill__dot { background: var(--cx-warn); }
.pill--danger  { color: var(--cx-danger-ink); background: var(--cx-danger-wash); }
.pill--danger  .pill__dot { background: var(--cx-danger); }
.pill--neutral { color: var(--cx-muted);      background: var(--cx-fill); }
.pill--neutral .pill__dot { background: var(--cx-faint); }
```

状态只有这三种是真状态，第四种用中性 pill。别拿状态色表装饰。

### 4.6 数据表

外层 `.table-wrap` 横向滚动，右缘渐隐提示还有列；窄屏再给一行小字。数字列右对齐、等宽数字。

```html
<div class="table-outer">
  <div class="table-wrap" tabindex="0" role="region" aria-label="任务列表">
    <table>
      <thead>
        <tr>
          <th scope="col">任务</th>
          <th scope="col">状态</th>
          <th scope="col" class="num">用时</th>
          <th scope="col" class="num">完成时间</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>把预览面板的滚动位置记进 localStorage</td>
          <td><span class="pill pill--ok"><span class="pill__dot"></span>通过</span></td>
          <td class="num">42 分</td>
          <td class="num">10-03 16:20</td>
        </tr>
        <tr>
          <td>任务卡评论在窄屏折行</td>
          <td><span class="pill pill--warn"><span class="pill__dot"></span>待验收</span></td>
          <td class="num">18 分</td>
          <td class="num">10-03 11:05</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<p class="scroll-hint">左右滑动看全部列</p>
```

```css
.table-outer { position: relative; }
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--cx-line);
  border-radius: var(--cx-radius-md);
  background: var(--cx-surface);
}
.table-wrap table { width: 100%; border-collapse: collapse; font-size: 14px; line-height: 20px; }
.table-wrap th, .table-wrap td {
  padding: 10px 14px; text-align: left; white-space: nowrap;
  border-bottom: 1px solid var(--cx-line);
}
.table-wrap thead th { font-size: 13px; font-weight: 600; color: var(--cx-muted); }
.table-wrap tbody tr:last-child td { border-bottom: 0; }
.table-wrap .num { text-align: right; font-variant-numeric: tabular-nums; }

/* 窄屏右缘渐隐 + 一行小字，提示还有列没显示。scroll-hint 表格和图共用 */
.scroll-hint { display: none; margin: 6px 0 0; font-size: 12px; line-height: 18px; color: var(--cx-faint); }
@media (max-width: 720px) {
  .table-outer::after {
    content: ""; position: absolute; top: 0; right: 0; bottom: 0; width: 32px;
    background: linear-gradient(to right, transparent, var(--cx-surface));
    pointer-events: none;
    border-top-right-radius: var(--cx-radius-md); border-bottom-right-radius: var(--cx-radius-md);
  }
  .scroll-hint { display: block; }
}
```

表格一律包在 `<div class="table-outer"><div class="table-wrap">…</div></div>` 里——外层的 `.table-outer` 是窄屏右缘渐隐的锚点，缺了它渐隐就没有对象；数据表孪生体里的表同样要包。最该看的列放在 400px 内先出现（通常是最左那一两列）。列多到读不动，就改成分组堆叠卡，别硬塞。表格文字不折行（`white-space: nowrap`），要折行的说明放表格外。

### 4.7 图 + 图注 + 数据表孪生体

内联 SVG 手写。**颜色走 `style="fill:var(--cx-…)"`**——`var()` 在 SVG 的呈现属性
（`fill="…"` / `stroke="…"`）里不生效，必须写进 `style`。图和它旁边那张数据表用同一个比例尺，
每个标签写图上真能到的值。

```html
<figure class="figure">
  <div class="figure-outer">
  <div class="figure-scroll">
    <svg viewBox="0 0 560 240" role="img" aria-label="四周内每周完成任务与新建任务数，完成任务稳定在 120 上下">
      <!-- 网格线用 --cx-line，文字用 --cx-muted，系列色走 style 里的 var() -->
      <g style="stroke:var(--cx-line)" stroke-width="1">
        <line x1="60" y1="20" x2="530" y2="20"/>
        <line x1="60" y1="63" x2="530" y2="63"/>
        <line x1="60" y1="105" x2="530" y2="105"/>
        <line x1="60" y1="148" x2="530" y2="148"/>
      </g>
      <line x1="60" y1="190" x2="530" y2="190" style="stroke:var(--cx-line-2)" stroke-width="1"/>
      <g style="fill:var(--cx-muted)" font-size="12" text-anchor="end" font-family="var(--cx-font-sans)">
        <text x="52" y="24">160</text><text x="52" y="67">120</text>
        <text x="52" y="109">80</text><text x="52" y="152">40</text><text x="52" y="194">0</text>
      </g>
      <g style="fill:var(--cx-text)">
        <rect x="82"  y="65" width="34" height="125" rx="2"/>
        <rect x="199" y="58" width="34" height="132" rx="2"/>
        <rect x="317" y="61" width="34" height="129" rx="2"/>
        <rect x="434" y="54" width="34" height="136" rx="2"/>
      </g>
      <g style="fill:var(--cx-muted)">
        <rect x="122" y="51" width="34" height="139" rx="2"/>
        <rect x="239" y="62" width="34" height="128" rx="2"/>
        <rect x="357" y="41" width="34" height="149" rx="2"/>
        <rect x="474" y="64" width="34" height="126" rx="2"/>
      </g>
      <g style="fill:var(--cx-muted)" font-size="12" text-anchor="middle" font-family="var(--cx-font-sans)">
        <text x="119" y="208">W38</text><text x="236" y="208">W39</text>
        <text x="354" y="208">W40</text><text x="471" y="208">W41</text>
      </g>
      <g font-size="12" font-family="var(--cx-font-sans)">
        <rect x="82" y="4" width="10" height="10" rx="2" style="fill:var(--cx-text)"/>
        <text x="98" y="13" style="fill:var(--cx-muted)">完成任务</text>
        <rect x="170" y="4" width="10" height="10" rx="2" style="fill:var(--cx-muted)"/>
        <text x="186" y="13" style="fill:var(--cx-muted)">新建任务</text>
      </g>
    </svg>
  </div>
  </div>
  <p class="scroll-hint">窄屏可左右滑动看图</p>
  <figcaption class="figcaption">图 1 · 四周内每周完成与新建任务数（个），数据见下方数据表。</figcaption>
  <details class="data-twin">
    <summary>查看数据表</summary>
    <div class="table-outer">
    <div class="table-wrap">
      <table>
        <thead><tr><th scope="col">周次</th><th scope="col" class="num">完成任务</th><th scope="col" class="num">新建任务</th></tr></thead>
        <tbody>
          <tr><td>W38</td><td class="num">118</td><td class="num">131</td></tr>
          <tr><td>W39</td><td class="num">124</td><td class="num">120</td></tr>
          <tr><td>W40</td><td class="num">121</td><td class="num">140</td></tr>
          <tr><td>W41</td><td class="num">128</td><td class="num">119</td></tr>
        </tbody>
      </table>
    </div>
    </div>
    <p class="scroll-hint">左右滑动看全部列</p>
  </details>
</figure>
```

```css
.figure { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.figure-outer { position: relative; }
.figure-scroll { overflow-x: auto; }
/* min-width 保图内文字 ≥11px，max-width 免宽屏上文字被放大到离谱 */
.figure svg { display: block; width: 100%; height: auto; min-width: 560px; max-width: 680px; }
.figcaption { font-size: 13px; line-height: 20px; color: var(--cx-muted); }
.data-twin { font-size: 13px; }
.data-twin > summary { cursor: pointer; color: var(--cx-muted); padding: 2px 0; }
.data-twin > summary:hover { color: var(--cx-text); }
.data-twin[open] > summary { margin-bottom: 8px; }
```

**窄屏要单独保图可读。** SVG 写成 `width:100%` 会随宽度等比缩小，图内文字跟着缩到读不清。做法是给
`<svg>` 一个 `min-width`（本参考用 560px，配 `max-width: 680px` 免得宽屏上文字被放大到比标题还大）配外层
`overflow-x: auto`，保证 400px 宽时**图内文字渲染后仍有约 11px 以上**（本参考在 400px 下量到 12px）。
交付前在 400px 下量一次。**每张图的同一小节里跟一张数据表**（放 `<details>` 里默认折叠，标题「查看数据表」），
它是读屏用户、键盘用户和想抄一个数的人唯一能拿到数值的地方。

**系列用色规则**：两条以内（大多数页面）优先用明度与线型区分——主系列 `--cx-text` 实线、副系列
`--cx-muted` 虚线，去色打印也分得清。柱、面、色块这类实心标记没有线型可借，就把明度差拉开一档：
主系列 `--cx-text`、副系列 `--cx-faint`，`--cx-muted` 和 `--cx-text` 太近，去色后会糊成一样。三条以上
分类系列才动 `--cx-chart-1`…`--cx-chart-6`，按顺序取，不要挑色。

### 4.8 callout（风险 / 说明）

```html
<div class="callout callout--note"><p class="callout__title">说明</p><p class="callout__body">数字取自每周五的快照，跨周的进行中任务不计入。</p></div>
<div class="callout callout--warn"><p class="callout__title">注意</p><p class="callout__body">W40 新建任务偏高，来自一次批量导入，不代表常态。</p></div>
<div class="callout callout--danger"><p class="callout__title">风险</p><p class="callout__body">运行时长已连续两周低于 300 小时，若下周仍低需排查机器可用性。</p></div>
```

```css
.callout {
  display: flex; flex-direction: column; gap: 4px;
  padding: 14px 18px;
  border: 1px solid var(--cx-line);
  border-radius: var(--cx-radius-lg);
}
.callout__title { margin: 0; font-size: 13px; line-height: 20px; font-weight: 600; }
.callout__body { margin: 0; font-size: 15px; line-height: 26px; color: var(--cx-text); }
.callout--note   { background: var(--cx-fill); }
.callout--note   .callout__title { color: var(--cx-muted); }
.callout--warn   { background: var(--cx-warn-wash);   border-color: transparent; }
.callout--warn   .callout__title { color: var(--cx-warn-ink); }
.callout--danger { background: var(--cx-danger-wash); border-color: transparent; }
.callout--danger .callout__title { color: var(--cx-danger-ink); }
```

### 4.9 代码块与行内代码

代码块反色：亮色下一块深底浅字，深色下改用 `--cx-code-bg`（= fill-2），不翻回亮色卡片。

```html
<pre class="code"><code>cheese show out/report.html --note "首版"</code></pre>
<p>行内代码：把 <code>--cx-accent</code> 只用于主操作。</p>
```

```css
.code {
  margin: 0; padding: 14px 16px;
  background: var(--cx-code-bg); color: var(--cx-code-ink);
  border-radius: var(--cx-radius-md);
  font-family: var(--cx-font-mono); font-size: 13px; line-height: 20px;
  overflow-x: auto;
}
code:not(pre code) {
  font-family: var(--cx-font-mono); font-size: 0.92em;
  background: var(--cx-fill); border: 1px solid var(--cx-line);
  border-radius: 4px; padding: 1px 4px;
}
```

### 4.10 目录（toc）

可选。长文档才加；短文档（就几节）删掉。序号是真实顺序时才用 `<ol>`。

```html
<nav class="toc" aria-label="目录">
  <p class="toc__label">目录</p>
  <ol>
    <li><a href="#tokens">颜色与 token</a></li>
    <li><a href="#type">版式</a></li>
    <li><a href="#components">组件</a></li>
  </ol>
</nav>
```

```css
.toc {
  display: flex; flex-direction: column; gap: 8px;
  padding: 16px 20px;
  border: 1px solid var(--cx-line);
  border-radius: var(--cx-radius-lg);
}
.toc__label { margin: 0; font-size: 13px; font-weight: 600; color: var(--cx-muted); }
.toc ol { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
.toc li::marker { color: var(--cx-faint); font-variant-numeric: tabular-nums; }
.toc a { color: var(--cx-text); text-decoration: none; }
.toc a:hover { color: var(--cx-accent-ink); }
```

### 4.11 按钮

```html
<button class="btn btn--primary" type="button">发布</button>
<button class="btn btn--secondary" type="button">取消</button>
```

```css
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 14px;
  border: 1px solid transparent;
  border-radius: var(--cx-radius-md);
  font-size: 14px; line-height: 20px; font-weight: 600;
  font-family: inherit; cursor: pointer;
}
.btn--primary { background: var(--cx-accent); color: var(--cx-on-accent); }
.btn--primary:hover { background: var(--cx-accent-press); }
.btn--secondary { background: transparent; border-color: var(--cx-line-2); color: var(--cx-text); }
.btn--secondary:hover { background: var(--cx-fill); }
```

同一组并排按钮里**只有一颗是琥珀**（「发布」是，「取消」不是）。这一页如果已经有 readout 占用了琥珀，就不要再放琥珀按钮——二选一。

### 4.12 文案

界面上的字也是设计材料。站在读者位置写：用人们认得的东西命名，不写系统内部怎么搭的。主动语态，
一个控件就说它会发生什么（按钮「发布」，之后「已发布」）。报错说清哪里错了、怎么修，不道歉、不含糊。
具体胜过机灵。短而直接。

不用这些腔调：破折号插话；「不是 X 而是 Y」的框式；先冒号后揭晓的句子；给生造的词加引号；
「值得注意的是」「老实说」这类套话。

中文排版：不用 `text-transform: uppercase`；不给汉字加 `letter-spacing`（纯拉丁字母数字的小标签可以加
一点点）；不用假斜体，强调用字重或颜色。空状态一律写「暂无 X」，不带句末标点。产品完整的中文规范见
`docs/design-system.md` §8。

---

## 5. 用法

**模板怎么用。** `templates/*.html` 的 `<style>` 用的就是这套 `--cx-*` token（第 2 节）；写新页面时照着
复制结构，按这一份内容的需要改 token 的值，组件的类名与结构照本参考 §4 取。所有模板共享这一套 token
和组件，区别只在版式：报告走 660px 正文栏和 `section` 流，看板走 1100px 和 `stat-row` + 图 + 表，数据表
走 `.table-wrap`，方案对比走并排卡（窄屏折成一列），一页纸把整页收进一张 A4，幻灯片一张一张铺在
1280×720 的舞台上（窄屏折成一列、缩放到宽度）。设计系统只定「长什么样」，不定「排什么」。

**可以按主题重调的：**

- **主色色相。** 琥珀是产品的，面对不同主题可以往主题自己的色相挪一点（比如一份关于海洋的报告用偏青的
  暖色），但**必须仍是单一、克制的一个强调色**，仍然只出现在一处，并且 `--cx-accent` / `-ink` / `-press` /
  `-wash` / `on-accent` 五个值仍然成套（`-ink` 要能在 wash 和 surface 上写字，`on-accent` 要能在 accent 上写字）。
  挪之前先量对比度：正文 ≥ 4.5:1，大字号与图形 ≥ 3:1。
- **中性色保留一点色相倾向。** 纯中灰显得没想过；本套灰带一点暖。换色相时整条灰阶一起挪，别只挪一两档。
- **字体分工。** 标题、正文、数据可以各点名不同族，但同页 CJK 族要统一，别正文 SC、表格 JP 混着来。
  中文衬线栈在多数 Linux / 容器 / 无头环境会静默回落成无衬线——想用衬线正文，先在目标环境里验它真生效。

**不可以改的：** 状态色三件套的语义；琥珀只给一处；卡片只描边不投影；`body` 显式设背景；组件只通过变量上色。

---

## 6. 常见错误

| 错误 | 为什么错 | 改成 |
|---|---|---|
| `color: #36383c` | 深色下不变，黑底黑字 | `var(--cx-text)` |
| `color: var(--cx-danger)` 写正文 | 记号色对比度不够 | `var(--cx-danger-ink)` |
| `background: #fff` | 深色下是一块白 | `var(--cx-surface)` |
| `border-radius: 10px` | 不在档位里 | `var(--cx-radius-*)` |
| `font-size: 10px` | 低于可读下限 | 至少 13px（图内文字 ≥ 11px） |
| `line-height: 1.6` | 不在档位里 | 配对给 20/26/28… |
| 卡片加 `box-shadow` | 违反「卡片只描边」 | `border: 1px solid var(--cx-line)` |
| 琥珀做普通图标 / 头像 / 装饰 | 稀释了唯一的主操作 | `--cx-muted` / `--cx-faint` |
| 圆角卡片左边加一条竖条 | AI 味清单里的样子 | 底纹 + 描边 + 间距 |
| `fill="var(--cx-chart-1)"` | SVG 呈现属性里 `var()` 失效 | `style="fill:var(--cx-chart-1)"` |
| 只量 `--cx-surface` 上的对比度 | 最不利的底是 `--cx-line-2` | 拿实际最深的底去量 |
| 引用一个没定义的变量 | 静默失效，`background` 简写还会让整条声明作废 | 先确认变量在 §2 里存在 |
