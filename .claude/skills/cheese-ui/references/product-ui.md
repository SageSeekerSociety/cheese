# 模式 A — 产品界面

用户登录后长时间工作的地方。目标不是"一眼惊艳"，是"看一天不累、信息一眼分得清"。
参照物是 Linear 和 Notion：克制本身就是专业感。

源文件：`frontend/src/style.css`（token + 工具类）、`frontend/src/plugins/vuetify.ts`
（Vuetify 主题镜像）。下面是意图说明，数值以源文件为准。

## 第一原则：95% 中性

**整屏约 95% 的像素是中性色。琥珀 `#F57F17` 是稀有强调色。**

这不是审美偏好，是信息设计：当页面上只有一个地方是彩色的，用户的眼睛立刻知道
该看哪儿。每多一处彩色，这个指示作用就衰减一分。全都强调，等于都不强调。

琥珀色**只允许**出现在这三个位置：

1. 当前屏幕的那**一个**主操作按钮
2. 激活态的导航指示
3. 品牌标记

明确不允许的地方（这些是踩过的坑，写在 vuetify.ts 注释里）：头像、状态 chip、
图标、选中行的填充色、链接、列表标记、正文强调。

Markdown / Tiptap 渲染出来的内容同理 —— 链接和列表符号保持中性墨色。琥珀是强调色，
不是正文颜色。

## Token

分四组，全部定义在 `style.css` 的 `:root`：

**中性色阶**（暖冷灰，从深到浅）
`--ink` 标题 → `--text` 正文 → `--muted` 次要 → `--faint` 元信息 →
`--line` / `--line-2` 发丝线 → `--fill` / `--fill-2` hover 与内嵌底 →
`--canvas` 应用背景 → `--surface` 卡片面板

**强调**：`--accent` / `--accent-press` / `--accent-ink`（浅底上的琥珀文字）/
`--accent-wash`（极少用的淡染）

**状态**：`--ok` / `--warn` / `--danger` —— 只给真实状态，不当装饰色用

**字体**：`--font-sans`（Inter 打头，中文回退 PingFang SC / 微软雅黑）、
`--font-display`（Bricolage Grotesque）、`--font-mono`（JetBrains Mono）

## 排版：五个档位，不要自创

字号字重不是随手定的，是一套刻意收窄的音阶。自己写 `font-size` 会打乱它。

| 类 | 用途 | 规格 |
|---|---|---|
| `.t-page-title` | 页面主标题 | 23px / 650 / -0.02em / display 字体 / `--ink` |
| `.t-title` | 区块、卡片标题 | 15px / 600 / `--ink` |
| `.t-eyebrow` | 眉标签 | 12px / 600 / +0.04em / `--faint` |
| `.t-body` | 正文 | 14px / 430 / 1.62 行高 / `--text` |
| `.t-meta` | 时间戳、#id、计数、分支名 | 12.5px / 等宽 / `--faint` / 等宽数字 |

`.t-eyebrow` 不要转成全大写 —— 中文没有大小写，转了只会让英文部分变吵。

`.t-meta` 用等宽数字（`tabular-nums`），所以列表里的数字会对齐。凡是会变化的数值
（耗时、计数、时间）都该用它，不然刷新时会左右跳动。

颜色不想套排版类时，用 `.c-ink` / `.c-text` / `.c-muted` / `.c-faint` /
`.c-ok` / `.c-warn` / `.c-danger`，不必为此新写 CSS。

## 状态怎么表达

**`.status-dot`（6px 圆点）+ 中性文字**，不是彩色 chip、不是彩色文字。

```html
<span class="status-dot status-dot--ok"></span>
<span class="t-body">运行中</span>
```

变体：`--ok` / `--warn` / `--danger` / `--muted`。

为什么不用彩色 chip：一个列表里如果每行都有一块彩色底，页面立刻变成圣诞树，
而且跟"唯一主操作按钮"抢注意力。圆点足够传达状态，又几乎不占视觉预算。

普通标签用 `.chip-neutral`：`--fill` 底、`--muted` 字、无边框、6px 圆角、12px。

## Vuetify 组件

主题已经把 token 镜像进去了（`plugins/vuetify.ts`），所以**组件默认样式就是对的**，
不要去覆盖它的颜色。

几个容易踩的点：

- `primary` 是琥珀，所以 `color="primary"` 等于在花掉那一次强调额度，想清楚再用
- `secondary` 被特意设成中性灰 `--muted` —— Vuetify 默认的天蓝色不属于这套语言
- `info` 也是中性灰，同理
- 产品**只有浅色主题**（`defaultTheme: 'light'`，不跟随系统），不要写深色变体
- 图标用 `@mdi/font`，已全局注册，直接 `mdi-xxx`

## 微交互

已经全局定义好了，通常不需要再写：

- `.v-btn` / `.v-chip` / `.v-list-item` / `.v-card` 有 0.15s 的背景、阴影、边框过渡
- 按钮 `:active` 下沉 0.5px —— 细微的按压反馈
- `:focus-visible` 是琥珀 55% 透明的 2px 描边，offset 2px。**别覆盖掉它**，
  这是键盘可用性的底线
- `::selection` 是琥珀 18% 淡染
- 滚动条统一细化过
- `prefers-reduced-motion` 下所有动画被压到 0.001ms

要加新动效，先问自己它替用户回答了什么问题。答不上来就别加 —— 多余动效是
"AI 生成感"最主要的来源之一。

## 手写 button 的坑

`button:not(.v-btn)` 被全局重置了（去掉浏览器默认边框和背景）。历史上这里出过
"神秘黑框"的 bug。所以手写的 `<button>` 必须自己完整定义外观，别指望默认样式。
