/**
 * 文档里那几种块在预览站里的条目：提示框、状态标签、时间线、指标卡、分栏、图表、
 * 流程图、折叠、公式、脚注，和手机上读表格的样子。
 *
 * 单独一份，和 `catalogRoom.ts` 同一个理由：`catalog.ts` 已经顶到一千行的上限。
 * 正文就是写作指南教给芝士的那套写法，打开这一页看到的，就是芝士照指南写出来的
 * 文档在编辑器里的样子；`MarkdownView` 那一条是同一份字在消息、文件、旧版本里的
 * 样子，两条应当长得一样。
 */
import type { CatalogEntry } from './catalog'

import { docPanelProps, docSession } from './catalogFixtures'

import MarkdownView from '@/components/common/MarkdownView.vue'
import PanelDocView from '@/components/panels/PanelDocView.vue'

const SAMPLE = `九月起搜索框改为常驻，下面是两周后的复盘。

> [!IMPORTANT]
> 搜索框改成常驻后，搜索次数翻倍[^1]，用户找到结果的比例没变：建议保留。

:::stats
- 日均搜索 | 2,140 次 | +104%
- 找到结果 | 71% | 持平
- 平均用时 | 9.2 秒 | -1.3 秒
:::

## 上线过程

:::timeline
- 9 月 2 日 | 灰度 10% {✓ 单元测试} {✓ 类型检查}
  只开给内部账号，观察三天。
- 9 月 5 日 | 全量上线 {! 等待复盘}
- 9 月 12 日 | 复盘
  搜索次数翻倍，找到结果的比例持平。
:::

## 两个方案

::::columns
:::column
**方案 A：保留常驻**

改动最小，已经上线。
:::
:::column
**方案 B：改回按钮**

需要一周，搜索次数会回落。
:::
::::

| 方案 | 工期 | 结论 |
| --- | --- | --- |
| 保留常驻 | 0 天 | {✓ 推荐} |
| 改回按钮 | 5 天 | {✗ 不做} |

:::chart line
| 周 | 改版前（次） | 改版后（次） |
| --- | --- | --- |
| 第 1 周 | 1,020 | 1,980 |
| 第 2 周 | 1,060 | 2,150 |
| 第 3 周 | 1,070 | 2,290 |
:::

搜索来自哪里：

:::chart pie
| 入口 | 占比 |
| --- | --- |
| 顶栏常驻 | 64% |
| 快捷键 | 23% |
| 侧栏按钮 | 13% |
:::

## 一次搜索怎么走

\`\`\`mermaid
flowchart LR
  A[输入关键词] --> B{有结果?}
  B -->|有| C[点开结果]
  B -->|没有| D[推荐相近词]
  D --> A
\`\`\`

<details><summary>增长率怎么算</summary>

增长率是 $\\frac{\\text{改版后} - \\text{改版前}}{\\text{改版前}}$，按周平均：

$$
\\frac{2140 - 1049}{1049} \\approx 104\\%
$$

</details>

[^1]: 改版前日均 1,049 次：2,140 ÷ 1,049 − 1 ≈ 104%。`

// 一条芝士的消息：和文档同样的写法，在对话里同样地画出来。
const CHAT_SAMPLE = [
  '<@zhangsan> 改版后一周的登录耗时 {✓ 已上线} {! 安卓待验证}',
  '',
  ':::chart line',
  '| 天  | 改版前（秒） | 改版后（秒） |',
  '| --- | ------ | ------ |',
  '| 周一 | 4.2    | 2.1    |',
  '| 周二 | 4.0    | 2.0    |',
  '| 周三 | 4.4    | 1.9    |',
  ':::',
  '',
  '> [!WARNING]',
  '> 安卓 9 以下周三前要回归一遍。',
  '',
  '```bash',
  'make e2e ANDROID=9',
  '```',
].join('\n')

export const DOC_BLOCK_ENTRIES: CatalogEntry[] = [
  {
    id: 'doc-blocks',
    title: 'PanelDocView · 块',
    about: '文档里的块：提示框、状态标签、时间线、指标卡、分栏、表格、图表、流程图、折叠、公式、脚注。',
    file: 'src/components/panels/doc/blocks/blockViews.ts',
    component: PanelDocView,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '能改',
        note: '点提示框的类型换一种；时间线圆点上有上移、下移、加一项、删除；光标进指标卡时末尾出现「＋」；图表点类型换一种，下面的表格就是数据；表格的行列把手加减行列；流程图点「源码」改，点「让芝士改」开出问芝士的框；选中字时浮条上有 ✓ ✗ !；行首或空格后打「/」插入块。',
        // 问芝士只开出输入框：预览站不连模型，发出去的请求一直等着。
        props: docPanelProps({
          session: docSession(SAMPLE),
          agentHandle: 'cheese',
          askAgent: () => new Promise(() => {}),
        }),
        expect: '上线过程',
      },
      {
        name: '只读',
        note: '控件都收起；图表的数据收在「数据」里；流程图点开全屏；脚注点开在原处看，文末不再列一遍；窄屏上文字表格变成卡片，数字表格横着滑、首列不动。',
        props: docPanelProps({ session: docSession(SAMPLE), editable: false, readOnly: true }),
        expect: '上线过程',
      },
    ],
  },
  {
    id: 'markdown-view',
    title: 'MarkdownView',
    about:
      '编辑器之外读一段 Markdown：消息、文件、周报、文档的旧版本。和文档用同一套块，应当和「PanelDocView · 块」的只读一格长得一样。',
    file: 'src/components/common/MarkdownView.vue',
    component: MarkdownView,
    needs: ['i18n'],
    states: [
      {
        name: '文档的读法',
        note: '文件、周报、旧版本：单个换行不断行。图表和流程图滚到眼前才画。',
        props: { source: SAMPLE },
        expect: '上线过程',
      },
      {
        name: '聊天的读法',
        note: '芝士的消息：单个换行就是换行；代码块右上角有「复制」；点名显示名字。',
        props: {
          source: CHAT_SAMPLE,
          as: 'chat',
          names: { mentionNames: { zhangsan: '张三' }, topicTitles: {} },
          copyCode: true,
        },
        expect: '安卓 9 以下周三前要回归一遍',
      },
    ],
  },
]
