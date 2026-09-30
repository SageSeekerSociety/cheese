/**
 * 模型管理那六件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，六条条目塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogRail.ts`、`catalogDashboard.ts` 同一个
 * 理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `MODELS_ENTRIES`
 * 这个值，运行时不构成循环。
 *
 * 为什么这六件值得一站：它们以前是一个 1428 行的页面里的三段 + 页头 + 那条横条 + 一个
 * 确认框，看其中任何一段都得先起假后端、把整页拉起来；拆开以后每一件只吃 props，于是
 * 每一件都能单独摆在预览站里看。数据见 `catalogModelsFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  MODELS_AUDIT,
  MODELS_EMPTY,
  MODELS_PROJECTS_EMPTY,
  MODELS_UNREACHABLE,
  modelsAuditProps,
  modelsBudgetsProps,
  modelsHeaderProps,
  modelsTableProps,
} from './catalogModelsFixtures'

import AdminModelsAudit from '@/components/admin/models/AdminModelsAudit.vue'
import AdminModelsBudgets from '@/components/admin/models/AdminModelsBudgets.vue'
import AdminModelsConfirmDialog from '@/components/admin/models/AdminModelsConfirmDialog.vue'
import AdminModelsFlash from '@/components/admin/models/AdminModelsFlash.vue'
import AdminModelsHeader from '@/components/admin/models/AdminModelsHeader.vue'
import AdminModelsTable from '@/components/admin/models/AdminModelsTable.vue'

/** 页头、表、额度、那条横条：Vuetify + 词条就够了（这几件不指路、不读 store）。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

/** 读不到网关时那句解释。两种原因（不可达 / 没配管理密钥）在页面里是两句，这里给一句。 */
const DOWN = '读不到网关的模型清单。确认网关在运行，然后重试。'

/** 服务端原话的样子（接口失败时那一格显示的就是它，页面不另写一句「加载失败」）。 */
const BOOM = 'connect ECONNREFUSED 10.0.0.4:4000'

export const MODELS_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-models-header',
    title: 'AdminModelsHeader',
    about: '模型管理页头：标题、一句说明（带当前窗口）、网关健康灯、窗口页签、刷新。',
    file: 'src/components/admin/models/AdminModelsHeader.vue',
    component: AdminModelsHeader,
    needs: UI,
    states: [
      {
        name: '网关活着',
        note: '健康灯是「常在的一眼」：灯说活没活，完整那句挂在 title 上（表格里那条说明才是解释）。窗口是**三段共用**的，所以它摆在页头。',
        props: modelsHeaderProps(),
        expect: 'ready',
      },
      {
        name: '网关读不到',
        note: '灯上只留一句短的（「网关读不到」），原因挂在 title 上 —— 两处写同一句话会让人以为是两次失败。',
        props: modelsHeaderProps({ health: { ok: false, text: '网关读不到', title: BOOM } }),
        expect: '网关读不到',
      },
      {
        name: '还没读到',
        note: '`health: null`（第一次读数还没回来）：灯那一格整个不画 —— 「还没读到」不是一种健康状态。',
        props: modelsHeaderProps({ health: null }),
        expect: '模型管理',
      },
    ],
  },
  {
    id: 'admin-models-table',
    title: 'AdminModelsTable',
    about: '模型那一段：网关上有哪些模型、上没上架、什么价、跑了多少。页面的主语。',
    file: 'src/components/admin/models/AdminModelsTable.vue',
    component: AdminModelsTable,
    needs: UI,
    states: [
      {
        name: '四个模型',
        note: '四种样子：常开的、config 来源只读的（给一个能点开改法的入口，不给一排点了报错的按钮）、停用的（带原因）、没定价的（说「未定价」和一个理由，不是 $0）。',
        props: modelsTableProps(),
        expect: 'GLM 4.7',
      },
      {
        name: '网关读不到',
        note: '网关不答话就没有可读的行：表体换成一条说明 + 重试，**不**画「暂无模型」（那把这个事实说反了）。',
        props: modelsTableProps({ models: MODELS_UNREACHABLE, state: 'error' }),
        expect: DOWN,
      },
      {
        name: '这一次读失败',
        note: '接口失败显示**服务端原话**，和「网关没配管理密钥」合成一处 —— 对读的人是同一个结果：这张表读不出来。',
        props: modelsTableProps({ models: null, state: 'error', error: BOOM }),
        expect: BOOM,
      },
      {
        name: '一个模型都没有',
        note: '读到了、确实是空的，才是「暂无模型」。和上面两格是三种不同的答案。',
        props: modelsTableProps({ models: MODELS_EMPTY }),
        expect: '暂无模型',
      },
      {
        name: '首次加载',
        note: '骨架每一列的形状和真行一样（到货那一刻不重排），一个数都不画。',
        props: modelsTableProps({ models: null, loading: true }),
      },
    ],
  },
  {
    id: 'admin-models-budgets',
    title: 'AdminModelsBudgets',
    about: '额度那一段：项目的网关 key 上那个「刹车值」，四档读数一眼看清。',
    file: 'src/components/admin/models/AdminModelsBudgets.vue',
    component: AdminModelsBudgets,
    needs: UI,
    states: [
      {
        name: '四个项目',
        note: '刹车值四档：有覆盖的、按额度折出来的、没设的、不限量的。不限量是一个结论，先说，别让读者自己去比 total 和 used。',
        props: modelsBudgetsProps(),
        expect: '不限量',
      },
      {
        name: '一个项目都没有',
        note: '读到了、确实是空的。',
        props: modelsBudgetsProps({ projects: MODELS_PROJECTS_EMPTY }),
        expect: '暂无项目',
      },
      {
        name: '读失败',
        note: '这一段自己拉、也自己失败：读不到时**不**退化成一张空表（空表说的是「还没有项目」），失败只由它自己这一次读决定。',
        props: modelsBudgetsProps({ projects: null, state: 'error', error: BOOM }),
        expect: BOOM,
      },
      {
        name: '首次加载',
        note: '和前两段各画各的骨架：一条请求慢不会让另两段一起空着。',
        props: modelsBudgetsProps({ projects: null, loading: true }),
      },
    ],
  },
  {
    id: 'admin-models-audit',
    title: 'AdminModelsAudit',
    about: '最近操作那一段：谁改了什么、成没成、改前改后差在哪。',
    file: 'src/components/admin/models/AdminModelsAudit.vue',
    component: AdminModelsAudit,
    needs: UI,
    states: [
      {
        name: '三条记录',
        note: '有快照的那条才画「查看改动」（before/after 都空的那几项没有字段变化可看）；失败那条把服务端原话挂在 detail 上。',
        props: modelsAuditProps(),
        expect: '查看改动',
      },
      {
        name: '展开一条改动',
        note: '展开哪一行由页面记（`expanded` 是按下标记的集合），diff 在 `AdminAuditDiff` 里画。',
        props: modelsAuditProps({ expanded: new Set([0]) }),
        expect: '收起',
      },
      {
        name: '暂无操作',
        note: '读到了、确实没有记录。',
        props: modelsAuditProps({ items: [] }),
        expect: '暂无操作记录',
      },
      {
        name: '读失败',
        note: '读不到时**不**显示「暂无操作」——那是把「没读到」说成「没有」。',
        props: modelsAuditProps({ items: [], error: BOOM }),
        expect: BOOM,
      },
      {
        name: '失败项',
        note: '失败是红的，而且带服务端原话 —— 「没改成」和「为什么没改成」是两件事，只画前一件等于把这页最需要的东西丢掉。',
        props: modelsAuditProps({ items: MODELS_AUDIT.slice(1, 2) }),
        expect: '失败',
      },
      {
        name: '没有快照的成功项',
        note: '这一条没有 before/after，所以那一行没有「查看改动」—— 摆一个只会点出一句「没有变化」的按钮是把人骗过去。',
        props: modelsAuditProps({ items: MODELS_AUDIT.slice(2, 3) }),
        expect: '改额度',
      },
      {
        name: '首次加载',
        note: '四根骨头，和真行一样高。',
        props: modelsAuditProps({ items: [], loading: true }),
      },
    ],
  },
  {
    id: 'admin-models-flash',
    title: 'AdminModelsFlash',
    about: '页顶那一条横条：写失败了（红）或写成功了（绿），一次只说一条。',
    file: 'src/components/admin/models/AdminModelsFlash.vue',
    component: AdminModelsFlash,
    needs: UI,
    states: [
      {
        name: '写失败',
        note: '`role="alert"`：服务端原话照抄。**读**失败不走这条 —— 那一条画在各自那一段的位置上。',
        props: { error: '网关拒绝了这次删除：这个模型正被 2 个项目路由着。', notice: null },
        expect: '网关拒绝了这次删除',
      },
      {
        name: '写成功',
        note: '`role="status"`（不是 alert）：说清刚动的是哪一个。两条同时有值时错误压过提示。',
        props: { error: null, notice: '已删除「gpt-4o」' },
        expect: '已删除',
      },
    ],
  },
  {
    id: 'admin-models-confirm-dialog',
    title: 'AdminModelsConfirmDialog',
    about: '删除 / 停用共用的确认框：说清后果、一个取消、一个主操作。',
    file: 'src/components/admin/models/AdminModelsConfirmDialog.vue',
    component: AdminModelsConfirmDialog,
    needs: UI,
    layout: true,
    teleport: true,
    states: [
      {
        name: '删除确认',
        note: '文案由页面给（名字要进那句话里），「那个主操作叫什么」也给（删除 / 停用 / 启用是三个词）—— 所以是同一件东西开两次。',
        props: {
          modelValue: true,
          title: '删除模型',
          body: '删掉「glm-4.7」？网关会立刻停止为它路由，这一步撤不回来。',
          confirmLabel: '删除',
          busy: false,
        },
        expect: '删除模型',
      },
      {
        name: '停用确认',
        note: '同一个框、另一句话：停用会把模型从选择器里摘掉，所以要说出来。',
        props: {
          modelValue: true,
          title: '停用模型',
          body: '停用「glm-4.7」？停用后它从选择器里消失，指向它的项目调它会被拒。',
          confirmLabel: '停用',
          busy: false,
        },
        expect: '停用模型',
      },
      {
        name: '正在写：关不掉',
        note: '`busy` 时框是 persistent、两个按钮都灰 —— 关掉框不会让请求停下来。',
        props: {
          modelValue: true,
          title: '删除模型',
          body: '删掉「glm-4.7」？网关会立刻停止为它路由，这一步撤不回来。',
          confirmLabel: '删除',
          busy: true,
        },
        expect: '删除模型',
      },
    ],
  },
]
