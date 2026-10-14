/**
 * 预览站里「拆出来的那半张画面」：这批的四个整页各拆成一个容器（`X.vue` —— 读地址、
 * 取数、发请求）加一个画面（`XView.vue` —— 只收 props、只发事件）。这里摆的是画面那一半，
 * 所以每一件都不带路由、不带接口就能单独渲染。
 *
 * 这一批有四件：功能数据页（认得出 / 认不出地址里那个功能 id）、一条反馈的全文、我的连接
 * （邮箱与飞书），以及项目的技能列表。条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要
 * 哪几样 / 看哪几格」，规矩见 `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到将近
 * 一千行的上限。这里的 `CatalogEntry` 是 type-only 引用，`catalog.ts` 反过来要
 * `SCENES_PAGES2_ENTRIES` 这个值，运行时不构成循环。数据见
 * `catalogScenesPages2Fixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  CONNECTION_PROJECTS,
  FEATURE,
  feedback,
  INTEGRATIONS,
  MAIL_DRAFTS,
  PRIVATE_FEEDBACK,
} from './catalogScenesPages2Fixtures'
import { noop } from './catalogScenesPagesFixtures'
import { skill } from './catalogSkills'

import { STATUS_LADDER } from '@/lib/feedbackMeta'
import AdminFeaturePageView from '@/views/admin/AdminFeaturePageView.vue'
import FeedbackDetailPageView from '@/views/feedback/FeedbackDetailPageView.vue'
import MyConnectionsViewView from '@/views/MyConnectionsViewView.vue'
import ProjectSkillsViewView from '@/views/ProjectSkillsViewView.vue'

/** 四件都要 Vuetify 的画法和一套语言包（页框、按钮、状态药丸、下拉、抽屉都在 Vuetify 上）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 技能那一页除了 `needs` 还要坐进 `v-layout`（抽屉是 `temporary` 的 navigation
 *  drawer），所以它那一件单独声明。 */
const UI_T_LAYOUT: CatalogNeed[] = ['vuetify', 'i18n']

/** 技能列表那一件里没有可点的去处（列表行自己 emit），不给任何回调的空实现。 */
const SKILLS_ARGS = {
  loading: false,
  loadError: '',
  skills: [],
  selectedId: null,
  selected: null,
  contents: null,
  revisions: null,
  busy: '',
  detailError: '',
  saving: false,
  formError: '',
  preview: null,
  reading: false,
  adding: false,
  importError: '',
  // 两个对话框的开关是 `defineModel`（必填）：收着的样子就是 `null` / `false`。
  editing: null,
  importing: false,
  // 画面只发事件，动作都在容器里；预览站上不给动作。
  open: noop,
  close: noop,
  save: noop,
  decline: noop,
  discard: noop,
  'start-edit': noop,
  restore: noop,
  delete: noop,
  'save-form': noop,
  'read-import': noop,
  'add-import': noop,
  back: noop,
}

/** 列表里第二份技能：一份保存下来、正在用的（上面那份是等确认的提议）。 */
const REPORT_SKILL = skill({ id: 'skill-2', name: 'report-format', title: '实验报告格式' })

export const SCENES_PAGES2_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-feature-page-view',
    title: 'AdminFeaturePageView',
    about:
      '后台「功能数据」里某一页的画面：注册表认得出地址里那个 id 就画它自己那一页；认不出就画一句「还没有这个功能的数据页」，并且把地址里那一段原样说出来 —— 打错字和还没做是两回事，两句话都要让人分得清。认 id、查注册表都在容器 `admin/AdminFeaturePage.vue` 里。',
    file: 'src/views/admin/AdminFeaturePageView.vue',
    component: AdminFeaturePageView,
    needs: UI_T,
    states: [
      {
        name: '认得出这个功能',
        note: '地址里是一个注册表里有的 id：这一页整个换成那个功能自己的页（预览站里是个替身，它各自要取数）。',
        props: { feature: FEATURE, id: 'docs-assistant' },
        expect: '这个功能自己那一页',
      },
      {
        name: '认不出的 id',
        note: '地址里那一段不在注册表里：页头照旧写「功能数据」，正文一句「还没有这个功能的数据页」，底下把打错的那个 id 摆出来。',
        props: { feature: undefined, id: 'unknown-thing' },
        expect: '地址里的 unknown-thing 不在后台的功能清单里',
      },
    ],
  },
  {
    id: 'my-connections-view-view',
    title: 'MyConnectionsViewView',
    about:
      '「我的连接」那一页的画面：上面一格是待发送的邮件（一封一封摊开，可以丢弃或发送），下面一格是已接入的账号（邮箱和飞书，各带一条「允许哪些项目的 AI 队友使用」和测试 / 删除）。取数、跳转、toast 都在容器 `MyConnectionsView.vue` 里，接入邮箱那张表单的草稿住在这一半 —— 它的每个字段都是这个文件里的一个元素。',
    file: 'src/views/MyConnectionsViewView.vue',
    component: MyConnectionsViewView,
    needs: UI_T,
    args: {
      integrations: [],
      drafts: [],
      projects: CONNECTION_PROJECTS,
      loading: false,
      error: '',
      busy: '',
      feishuMissing: false,
      feishuAsking: false,
      refresh: noop,
      discard: noop,
      send: noop,
      'connect-feishu': noop,
      'set-grants': noop,
      recheck: noop,
      authorize: noop,
      remove: noop,
      save: (_body: Record<string, unknown>, done: (ok: boolean, message?: string) => void) => done(true),
    },
    states: [
      {
        name: '两张卡都空着',
        note: '刚接入平台、什么都还没有：两格各说一句自己那一句，不是一片空白。',
        props: {},
        expect: '暂无待发送的邮件',
      },
      {
        name: '两封等着发的邮件',
        note: 'AI 队友写好的草稿在这里等人过目：收件人、抄送、主题、正文、附件、来自哪个项目，一行一项；每封两颗按钮（丢弃 / 发送）。',
        props: { drafts: MAIL_DRAFTS },
        expect: '第一周的作业安排',
      },
      {
        name: '连着两个账号，飞书那个还没授权',
        note: '一条邮箱、一条走平台应用的飞书：飞书那条在授权回来之前什么也做不了，所以它那一行说的是「还没连接飞书」，而不是旁边那颗「正常」药丸的意思。',
        props: { integrations: INTEGRATIONS },
        expect: '还没连接飞书',
      },
      {
        name: '平台没配飞书应用',
        note: '管理员还没在这一部署里填飞书应用：「连接飞书」灰着，底下写明为什么 —— 灰按钮没有理由，人只会当成坏了。',
        props: { integrations: [INTEGRATIONS[0]], feishuMissing: true },
        expect: '管理员还没配置飞书应用',
      },
      {
        name: '读失败',
        note: '整页没读出来：错误那句话挂在页头下面，两格照旧在（不是把整页换成一片空白）。',
        props: { error: '加载连接失败' },
        expect: '加载连接失败',
      },
      {
        name: '正在读',
        note: '还在路上：页头那颗刷新转着，两格先按空的样子摆着。',
        props: { loading: true },
        expectSelector: '.v-progress-circular',
      },
    ],
  },
  {
    id: 'feedback-detail-page-view',
    title: 'FeedbackDetailPageView',
    about:
      '一条反馈的全文那一页：左边是这条反馈自己（标题、状态与标签、作者、正文的几段、评论区），右边是它走到哪儿了（进展、领没领、支持与评论数、来源）。取数、地址、删除和评论请求都在容器 `feedback/FeedbackDetailPage.vue` 里；评论框自己那份草稿和焦点留在这一半，因为框里那个元素就在这个文件里。',
    file: 'src/views/feedback/FeedbackDetailPageView.vue',
    component: FeedbackDetailPageView,
    needs: UI_T,
    args: {
      item: null,
      claim: null,
      loading: false,
      error: null,
      gone: false,
      statusLadder: STATUS_LADDER,
      moreCommentsLoading: false,
      moreRepliesLoading: {},
      deleting: false,
      posting: false,
      claiming: false,
      // 删除确认是这个文件里就地换掉那一行的（不开弹窗），开关走 `defineModel`。
      confirmingDelete: false,
      back: noop,
      'dismiss-error': noop,
      reply: noop,
      like: noop,
      'remove-comment': noop,
      'load-more-comments': noop,
      'load-more-replies': noop,
      post: noop,
      'toggle-support': noop,
      share: noop,
      'change-claim': noop,
      'delete-confirm': noop,
    },
    states: [
      {
        name: '正在读',
        note: '还没问出结果之前也画骨架：先画「这条反馈不存在」再换成内容，等于先说错一句话再收回去。',
        props: { loading: true },
        expectSelector: '.skel--detail',
      },
      {
        name: '没读到',
        note: '读失败，但这条反馈还在：色块里说「详情加载失败」，底下把服务端的原话摆出来，动作是回反馈中心。',
        props: { error: '网络不通', gone: false },
        expect: '详情加载失败',
      },
      {
        name: '这条不存在',
        note: '失败之后又问了一次，服务端说 403/404：换成「这条反馈不存在」，服务端那句原话不再画 —— 在 404 这一态它只是把上面那句换个说法再说一遍。',
        props: { error: '反馈不存在', gone: true },
        expect: '这条反馈不存在',
      },
      {
        name: '一条公开的反馈',
        note: '读到了：页头是标题、状态、类型、标签和作者，正文按「问题描述 / 为什么需要 / 期望方案 / 现场」分段，右边是进展卡和两个计数，底下一整条支持按钮。',
        props: { item: feedback(), claim: feedback() },
        expect: '问题描述',
      },
      {
        name: '已经支持过',
        note: '同一条，这次已经支持过：按钮换成「已支持」，连着图标一起变成实心的那一版。',
        props: { item: feedback({ supported: true }), claim: feedback({ supported: true }) },
        expect: '已支持',
      },
      {
        name: '私密的那条',
        note: '私密反馈：状态那一行多一颗「私密」，右上角写着它只有谁能看；支持按钮和评论框都不摆（这一页不是给同事看的）。',
        props: { item: PRIVATE_FEEDBACK, claim: PRIVATE_FEEDBACK },
        expect: '私密',
      },
      {
        name: '正在确认删除',
        note: '标题右边那颗「删除」按下去：就地换成一句问话加「确认删除 / 取消」两颗 —— 不开弹窗，一次误触的代价是这一条。',
        props: { item: feedback(), claim: feedback(), confirmingDelete: true },
        expect: '确认删除',
      },
    ],
  },
  {
    id: 'project-skills-view-view',
    title: 'ProjectSkillsViewView',
    about:
      '项目「技能」那一页的画面：上面一格是等确认的提议（芝士新提的或改过的），下面一格是正在用的技能，点一行从右边滑出详情抽屉。取数、地址、页头的动作和删除确认都在容器 `ProjectSkillsView.vue` 里 —— 画面只吃 props、只发事件。',
    file: 'src/views/ProjectSkillsViewView.vue',
    component: ProjectSkillsViewView,
    needs: UI_T_LAYOUT,
    // 详情抽屉是 Vuetify 的 `v-navigation-drawer`（`temporary`），要一个 `v-layout` 祖先。
    layout: true,
    args: SKILLS_ARGS,
    states: [
      {
        name: '还没有技能',
        note: '一个技能都没有：两格都不画，只有一句「暂无技能」。',
        props: {},
        expect: '暂无技能',
      },
      {
        name: '一份待确认的、一份在用的',
        note: '上面一格摆芝士提议的那一份（署名是提议的人），下面一格摆正在用的那份，右边写着「第 2 版 · 9月30日」。',
        props: { skills: [skill({ state: 'draft' }), REPORT_SKILL] },
        expect: '第 2 版 · 9月30日',
      },
      {
        name: '读失败',
        note: '没读出来：页头下面一条能看见的错误，空态不摆 —— 读失败和本来就没有是两回事。',
        props: { loadError: '读取失败' },
        expect: '读取失败',
      },
      {
        name: '正在读',
        note: '还在路上：一颗转着的圈，两格都先不摆。',
        props: { loading: true },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '打开一份技能',
        note: '点了一行：详情抽屉从右边滑出来，抬头写着调用名和第几版，下面是用途和全文 —— 认得出打开的是这一份。',
        props: {
          skills: [skill({ state: 'draft' }), REPORT_SKILL],
          selectedId: REPORT_SKILL.id,
          selected: REPORT_SKILL,
          contents: {},
          revisions: [],
        },
        expect: '调用名 report-format',
      },
    ],
  },
]
