/**
 * 「技能详情抽屉」那一件在预览站里的条目。
 *
 * 一份技能从右边滑出来：上面是芝士提议的依据（只有它提议的才有），下面是全文，另一页签
 * 是历史版本，动作放在底部。数据和动作都在页面那一层，这一件只吃 props、只发事件，
 * 于是能单独摆在预览站里。
 *
 * 它和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogRoom.ts`、
 * `catalogFeedback.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `SKILL_ENTRIES`
 * 这个值，运行时不构成循环。数据（一份真实的技能）就在这份里，形状照 `lib/projectSkill`
 * 和 `ProjectSkillsView.spec.ts` 造。
 */
import type { ProjectSkill } from '@/lib/projectSkill'
import type { CatalogEntry, CatalogNeed } from './catalog'

import SkillDetailDrawer from '@/components/skills/SkillDetailDrawer.vue'

/** 抽屉本体（`v-navigation-drawer`）、页签、`v-chip`、`v-spacer`、按钮（`BaseButton` 是
 *  `v-btn`）、等待历史版本那颗转圈，都要 Vuetify。模板里还有 `<i18n-t>`（抬头那行署名
 *  和历史版本那一行）—— 它是 i18n 插件注册的全局组件，所以 i18n 也要装；文字和日期
 *  走全局的 `t()` / i18n 单例。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 一份技能：默认是已经保存、正在用的那一版（`state: 'active'`），配套文件是空的。 */
function skill(over: Partial<ProjectSkill> = {}): ProjectSkill {
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
]
