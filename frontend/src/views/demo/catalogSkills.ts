/**
 * 技能相关的三件在预览站里的条目：「技能详情抽屉」「队友技能面板」和「技能市场」。
 *
 * 抽屉：一份技能从右边滑出来，上面是芝士提议的依据（只有它提议的才有），下面是全文，
 * 另一页签是历史版本，动作放在底部。面板：一个队友带哪些技能、还能从哪儿加。市场：
 * 两家厂商公开的技能 / 插件 / MCP 各一张卡片栅格，卡片上的 + 就是「取一份到这台机器」。
 * 数据和动作都在页面那一层，这三件只吃 props、只发事件，于是能单独摆在预览站里。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogRoom.ts`、
 * `catalogFeedback.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `SKILL_ENTRIES`
 * 这个值，运行时不构成循环。数据（一份真实的技能）就在这份里，形状照 `lib/projectSkill`
 * 和 `ProjectSkillsView.spec.ts` 造。
 */
import type { ProjectSkill } from '@/lib/projectSkill'
import type { CatalogEntry, CatalogNeed } from './catalog'

import AgentSkillsDialog from '@/components/agents/AgentSkillsDialog.vue'
import SkillDetailDrawer from '@/components/skills/SkillDetailDrawer.vue'
import SkillMarket from '@/components/skills/SkillMarket.vue'

/** 抽屉本体（`v-navigation-drawer`）、页签、`v-chip`、`v-spacer`、按钮（`BaseButton` 是
 *  `v-btn`）、等待历史版本那颗转圈，都要 Vuetify。模板里还有 `<i18n-t>`（抬头那行署名
 *  和历史版本那一行）—— 它是 i18n 插件注册的全局组件，所以 i18n 也要装；文字和日期
 *  走全局的 `t()` / i18n 单例。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 一份技能：默认是已经保存、正在用的那一版（`state: 'active'`），配套文件是空的。
 *  `catalogScenesPages2.ts`（技能列表那一页的几个状态）也拿它造数据。 */
export function skill(over: Partial<ProjectSkill> = {}): ProjectSkill {
  return {
    id: 'skill-weekly',
    project_id: 'p1',
    name: 'weekly-report',
    title: '项目周报',
    description: '把这一周的进度理成一段话，周五发到频道里。',
    body: '## 步骤\n\n1. 把这周的提交读一遍。\n2. 按「做完了 / 在做 / 卡住」分三段写。\n3. 发到本项目的频道里。\n',
    files: {},
    state: 'active',
    origin: 'cheese',
    shipped_revision: 2,
    proposed_by: 'cheese',
    confirmed_by: 'alice',
    confirmed_at: '2026-09-30T09:00:00Z',
    source_topic_id: null,
    proposal: null,
    created_at: '2026-09-20T09:00:00Z',
    updated_at: '2026-09-30T09:00:00Z',
    ...over,
  }
}

/** 一份内容长的技能：正文长、带着配套文件，抽屉里要滚起来。 */
const LONG_SKILL = skill({
  id: 'skill-catalog',
  name: 'catalog-writer',
  title: '把组件补进预览站',
  description: '按目录站的规矩给一件组件补一份条目和几格状态。',
  body: [
    '## 需要的输入',
    '',
    '- 一份要入库的 `.vue` 的路径。',
    '- 它自己那份 `.spec.ts`（固定数据照那里造）。',
    '',
    '## 步骤与规则',
    '',
    '1. 读一遍组件，认清楚它吃哪些 props、走哪几条岔路。',
    '2. 每一格给一句只有这一格才有的 `expect`，别拿两格都有的字充数。',
    '3. `needs` 只写它真正要的插件 —— 多写不报错，少写当场红。',
    '4. 跑 `pnpm exec vitest run src/views/demo/catalog.spec.ts`。',
    '',
    '## 输出要求',
    '',
    '条目落在 `catalog*.ts`，固定数据落在同名 `*Fixtures.ts`。',
  ].join('\n'),
  shipped_revision: 5,
  files: {
    'SKILL.md': { sha256: 'a1', size: 2048 },
    'scripts/scaffold.mjs': { sha256: 'b2', size: 512 },
  },
})

/** 队友技能面板的池子：平台随会话发货的原生技能，加上这台机器上的。名字就是
 *  技能的名字（后端按名字点亮、按名字取说明书），说明就是那一格的第二行。 */
const SKILL_POOL = [
  { name: 'cheese', description: '芝士自己的用法', source: 'platform' as const },
  { name: 'wolfram', description: '用 Wolfram 把算式算准', source: 'platform' as const },
  { name: 'ponytail', description: '懒，但把活干对的写法', source: 'machine' as const },
  { name: 'documents', description: '读写办公文档与 PDF', source: 'platform' as const },
  { name: 'showcase', description: '制作网页、看板和图表', source: 'platform' as const },
  { name: 'skill-creator', description: '创建或修改技能', source: 'platform' as const },
  { name: 'mcp-builder', description: '构建 MCP 服务和外部 API 接入', source: 'platform' as const },
]

/** 平台技能的探测结果：只有可用时对应灯板点亮。 */
const SKILL_HEALTH = Object.fromEntries(
  SKILL_POOL.filter((skill) => skill.source === 'platform').map((skill) => [
    skill.name,
    { status: 'ok' as const, detail: '可用' },
  ])
)

/** 市场的一份目录：技能 / 插件各一条能装的，外加一条只读的 MCP。`plugin_total` 比摆出来
 *  的多 —— 插件页签那句「列出前 N 条，共 M 条」要用它。名字、说明就是卡片上的两行。 */
export const SKILL_MARKET = {
  skills: [
    {
      name: 'pdf',
      description: '把 PDF 里的文字和表格读出来。',
      url: 'https://github.com/anthropics/skills/tree/main/pdf',
      source: 'anthropic',
      kind: 'skill' as const,
    },
  ],
  plugins: [
    {
      name: 'example-plugin',
      description: '一个示例插件，带一条斜杠命令。',
      url: 'https://github.com/anthropics/claude-plugins/tree/main/example',
      source: 'anthropic',
      kind: 'plugin' as const,
    },
  ],
  plugin_total: 12,
  mcp: [
    {
      name: 'filesystem',
      description: '读写本机文件的一台 MCP server。',
      url: '',
      source: 'mcp',
      kind: 'mcp' as const,
    },
  ],
  notes: [],
}

export const SKILL_ENTRIES: CatalogEntry[] = [
  {
    id: 'skill-detail-drawer',
    title: 'SkillDetailDrawer',
    about:
      '一份技能的详情，从右边滑出来：已保存的写着「第几版」，芝士提议的还多一段依据；下面是全文和配套文件，另一页签是历史版本，动作在底部。',
    file: 'src/components/skills/SkillDetailDrawer.vue',
    component: SkillDetailDrawer,
    needs: UI_T,
    // 抽屉本体是 Vuetify 的 `v-navigation-drawer`：它要一个 `v-layout` 祖先（`temporary`
    // 也从那儿读布局），所以这一件坐在布局里，别的条目那一页没有这层壳。给出 `skill`
    // 就是打开着的样子。
    layout: true,
    states: [
      {
        name: '打开着一份技能',
        note: '已经保存、正在用的那一版：抬头写「第 2 版 · 9月30日」，下面是用途和全文；底部是「删除 / 修改」两颗 —— 没有待确认的东西要保存。',
        props: { skill: skill(), contents: {}, revisions: [], busy: '', error: '' },
        expect: '把这一周的进度理成一段话，周五发到频道里。',
      },
      {
        name: '内容长的一份',
        note: '正文长、还带着两个配套文件：正文区自己滚，文件按路径排成一列、各挂一个大小。抽屉宽度跟着视口收（手机上就是整屏）。',
        props: {
          skill: LONG_SKILL,
          contents: { 'SKILL.md': '# 把组件补进预览站\n' },
          revisions: [],
          busy: '',
          error: '',
        },
        expect: 'scripts/scaffold.mjs',
      },
    ],
  },
  {
    id: 'agent-skills-dialog',
    title: 'AgentSkillsDialog',
    about: '一个 AI 队友带哪些技能：黄底一块灯板，展示队友装备的技能；灯亮表示当前可用，灯灭表示不可用或状态未知。',
    file: 'src/components/agents/AgentSkillsDialog.vue',
    component: AgentSkillsDialog,
    needs: UI_T,
    teleport: true,
    args: { modelValue: true },
    states: [
      {
        name: '一个队友带的',
        note: '6 个内置技能始终属于每位队友；灯亮表示当前可用。',
        props: {
          agentName: '米洛',
          carried: [SKILL_POOL[1]],
          available: SKILL_POOL.filter((skill) => skill.source === 'platform' && skill.name !== 'wolfram'),
          health: SKILL_HEALTH,
          deviceId: null,
        },
        expect: 'wolfram',
      },
      {
        name: '绑了机器时',
        note: '6 个内置技能和机器上的 ponytail 都显示在灯板；灯亮表示当前可用。',
        props: {
          agentName: '米洛',
          carried: [SKILL_POOL[2]],
          available: SKILL_POOL.filter((skill) => skill.name !== 'ponytail'),
          health: SKILL_HEALTH,
          deviceId: 'dev1',
        },
        expect: 'ponytail',
      },
      {
        name: '还在读技能',
        note: '池子还没到：底板照画，一句话替掉那一格，而不是一片空白。',
        props: {
          agentName: '米洛',
          carried: [],
          available: [],
          health: {},
          deviceId: null,
          loading: true,
        },
        expect: '正在读技能',
      },
    ],
  },
  {
    id: 'skill-market',
    title: 'SkillMarket',
    about:
      '技能市场：两家厂商公开的技能 / 插件 / MCP 各一张卡片栅格，卡片上的 + 就是「取一份到这台机器上」。页签停在哪儿、装到哪台机器都由页面给；没机器时 + 按不动。第四个货架「Agent 模板」只在市场里摆（`showAgents`）。',
    file: 'src/components/skills/SkillMarket.vue',
    component: SkillMarket,
    // 页签与 `v-icon` 是 Vuetify 的；卡片上的字走全局 `t()`。
    needs: UI_T,
    args: { market: SKILL_MARKET, deviceId: 'dev1' },
    states: [
      {
        name: '技能页签，有货品',
        note: '停在技能：一条能装的技能一张卡，右下角一颗 + 挂「装到这台机器上」；卡片刻着来源。',
        props: {},
        expect: '把 PDF 里的文字和表格读出来。',
      },
      {
        name: '插件页签',
        note: '停在插件：卡片之外多一句「列出前几条、官方目录共几条」——摆出来的不是全部。',
        props: { tab: 'plugins' },
        expect: '官方目录共 12 条',
      },
      {
        name: '没机器，+ 按不动',
        note: '没绑工作电脑：卡片照画，+ 是灰的（落盘总得有个落点）——不是把卡片藏起来。',
        props: { deviceId: null },
        expectSelector: '.mcard__add[disabled]',
      },
      {
        name: 'Agent 模板那一格',
        note: '摆第四个货架时停在「Agent 模板」：后台还没有这类东西，只照实说一句，不摆假卡片。',
        props: { showAgents: true, tab: 'agents' },
        expect: '还没有 Agent 模板。',
      },
    ],
  },
]
