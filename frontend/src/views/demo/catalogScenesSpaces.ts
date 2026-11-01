/**
 * 预览站里「题目板拆出来的那半张画面」这一组：`views/spaces/detail/` 那几个路由页各拆成
 * 一个页面（`X.vue` —— 读地址、取数、跳转）加一个画面（`XView.vue` —— 只收 props、只发
 * 事件）。这里摆的是画面那一半，所以每一件都不带路由、不带接口就能单独渲染。
 *
 * 这一组有七件：待审核题目、分类管理、域名组管理、题目模板管理、邀请码、分类话题，以及
 * 它们共用的那只话题选择器（`TopicSelectorView.vue`，只认 props —— 搜索与新建都由容器
 * 通过回调传进来）。条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，
 * 规矩见 `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到将近一千行的上限。这里的
 * `CatalogEntry` 是 type-only 引用，`catalog.ts` 反过来要 `SCENES_SPACES_ENTRIES` 这个值，
 * 运行时不构成循环。数据见 `catalogScenesSpacesFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  auditTask,
  domainGroup,
  inviteCode,
  inviteRefTo,
  spaceCategory,
  spaceTemplate,
  topic,
  topicCandidates,
} from './catalogScenesSpacesFixtures'
import { AGENT_TYPES } from './catalogSharedFixtures'

import TopicSelectorView from '@/components/common/TopicSelectorView.vue'
import AuditTaskView from '@/views/spaces/detail/AuditTaskView.vue'
import InviteCodesView from '@/views/spaces/detail/InviteCodesView.vue'
import ManageCategoriesView from '@/views/spaces/detail/ManageCategoriesView.vue'
import ManageDomainGroupsView from '@/views/spaces/detail/ManageDomainGroupsView.vue'
import ManageTemplatesView from '@/views/spaces/detail/ManageTemplatesView.vue'
import ManageTopicsView from '@/views/spaces/detail/ManageTopicsView.vue'

/** 七件都要 Vuetify 的画法和一套语言包。去处（进某个人的主页、去某一页）由容器给，
 *  画面自己没有路由就退化成点不动的字，照旧画得出来。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 话题选择器的两个回调：搜索与新建。画面在原时机（失焦、输入变化）调它们。 */
const TOPIC_CALLBACKS = {
  searchTopics: async (query: string) => topicCandidates(query),
  createTopic: async () => 99,
}

export const SCENES_SPACES_ENTRIES: CatalogEntry[] = [
  {
    id: 'audit-task-view',
    title: 'AuditTaskView',
    about:
      '「待审核题目」那一页的画面：一列等审的题，点一行摊开看提交要求，通过 / 驳回两颗按钮。翻页、通过/驳回、驳回原因对话框都在容器 `AuditTask.vue` 里；展开哪一行是画面自己的状态。',
    file: 'src/views/spaces/detail/AuditTaskView.vue',
    component: AuditTaskView,
    needs: UI_T,
    args: {
      tasks: [],
      failed: false,
      errorReason: null,
      forbidden: false,
      loadingMore: false,
      hasMore: false,
      refreshing: false,
      total: 0,
    },
    states: [
      {
        name: '还没有要审的题',
        note: '一条都没有：就地说一句「暂无待审核的题目」，不是一块空白。',
        props: {},
        expect: '暂无待审核的题目',
      },
      {
        name: '队列里有题',
        note: '两行等审的题，各写着题名、出题人和提交时间，右边两颗通过 / 驳回。',
        props: { tasks: [auditTask(), auditTask({ id: 102, name: '并查集' })], total: 2 },
        expect: '最短路径',
      },
      {
        name: '读失败',
        note: '列表没读出来：错误替换整个列表（`tasks` 是空的，光看「空」分不出读失败和本来就空）。',
        props: { failed: true, errorReason: '加载待审核题目失败' },
        expect: '加载待审核题目失败',
      },
    ],
  },
  {
    id: 'manage-categories-view',
    title: 'ManageCategoriesView',
    about:
      '「分类管理」那一屏的画面：一列分类、每行的 ⋯（归档 / 删除）、新建/编辑那个表单，和一根「添加分类」。读分类、真增删改、默认分类的切换都在容器 `ManageCategories.vue` 里。',
    file: 'src/views/spaces/detail/ManageCategoriesView.vue',
    component: ManageCategoriesView,
    needs: UI_T,
    args: {
      categories: [],
      defaultCategoryId: null,
      loading: false,
      saving: false,
      failed: false,
      errorReason: null,
      forbidden: false,
      open: false,
      agentTypes: AGENT_TYPES,
      agentTypesFailed: false,
    },
    states: [
      {
        name: '还没有分类',
        note: '一个分类都没有：就地说一句「暂无分类」，并摆着「添加分类」那颗按钮。',
        props: {},
        expect: '暂无分类',
      },
      {
        name: '列着分类',
        note: '两个分类，各写着名字和描述；归档过的那一行没有铅笔和星标，只剩 ⋯。',
        props: {
          categories: [spaceCategory(), spaceCategory({ id: 2, name: '数据结构', archivedAt: 1757000001000 })],
          defaultCategoryId: 1,
        },
        expect: '算法基础',
      },
      {
        name: '读分类失败',
        note: '列表没读出来：错误替换整个列表区，不是「暂无分类」—— 那会让失败看着像本来就没有。',
        props: { failed: true, errorReason: '读取分类失败' },
        expect: '读取分类失败',
      },
    ],
  },
  {
    id: 'manage-domain-groups-view',
    title: 'ManageDomainGroupsView',
    about:
      '「域名组管理」那一屏的画面：一列域名组、每行的编辑 / 删除、新建/编辑那个表单（一组域名，可增可减）。读列表、真增删改都归容器 `ManageDomainGroups.vue`。',
    file: 'src/views/spaces/detail/ManageDomainGroupsView.vue',
    component: ManageDomainGroupsView,
    needs: UI_T,
    args: { domainGroups: [], loading: false, saving: false, open: false },
    states: [
      {
        name: '还没有域名组',
        note: '一个都没有：就地说一句「暂无域名组」，并摆着「创建域名组」那颗按钮。',
        props: {},
        expect: '暂无域名组',
      },
      {
        name: '列着域名组',
        note: '两个域名组，各写着名字和描述，右边编辑 / 删除两颗。',
        props: {
          domainGroups: [domainGroup(), domainGroup({ id: 2, name: '合作院校', domains: ['partner.edu'] })],
        },
        expect: '本校',
      },
      {
        name: '在读',
        note: '列表还在路上的时候只有一颗转圈。',
        props: { loading: true },
        expectSelector: '.v-progress-circular',
      },
    ],
  },
  {
    id: 'manage-templates-view',
    title: 'ManageTemplatesView',
    about:
      '「题目模板管理」那一屏的画面：一列模板，每行写着它定下的那几项（个人/团队、难度、默认限时天数、是否要实名），右边编辑 / 删除。去新建/编辑页、真删都归容器 `ManageTemplates.vue`。',
    file: 'src/views/spaces/detail/ManageTemplatesView.vue',
    component: ManageTemplatesView,
    needs: UI_T,
    args: { templates: [] },
    states: [
      {
        name: '还没有模板',
        note: '一个模板都没有：就地说一句「暂无题目模板」，并摆着「创建模板」那颗按钮。',
        props: {},
        expect: '暂无题目模板',
      },
      {
        name: '列着模板',
        note: '两个模板，各写着名字和它定下的那几项。',
        props: {
          templates: [
            spaceTemplate(),
            spaceTemplate({ name: '团队题', submitterType: 'TEAM', rank: 3, requireRealName: false }),
          ],
        },
        expect: '个人题',
      },
    ],
  },
  {
    id: 'invite-codes-view',
    title: 'InviteCodesView',
    about:
      '「邀请码」那一页的画面：最上面单列「当前使用中的码」，下面一列每张码的用量、有效期、建码人和说明，每行可复制、就地调整、撤销（点两下）。读列表、改、撤、建都 emit 给容器 `InviteCodes.vue`。',
    file: 'src/views/spaces/detail/InviteCodesView.vue',
    component: InviteCodesView,
    needs: UI_T,
    args: {
      codes: [],
      loading: false,
      failed: false,
      errorDetail: null,
      busy: false,
      userRefTo: inviteRefTo,
      newOpen: false,
      confirmingId: null,
      editingId: null,
      copiedId: null,
      newMaxUses: '1',
      newExpiresOn: '',
      newNote: '',
      editMaxUses: '1',
      editExpiresOn: '',
      editNote: '',
    },
    states: [
      {
        name: '还没有码',
        note: '一张都没有：就地说一句「暂无邀请码」，并摆着「新建」那颗按钮。',
        props: {},
        expect: '暂无邀请码',
      },
      {
        name: '有两张码',
        note: '一张还能用（单列在「当前使用中的码」里），一张已经用尽（躺在列表里，标着「已用尽」）。',
        props: { codes: [inviteCode(), inviteCode({ id: 2, code: 'ZZZZZZZZ', maxUses: 10, useCount: 10 })] },
        expect: 'K7PM3QXW',
      },
      {
        name: '在读',
        note: '列表还在路上的时候只有一颗转圈。',
        props: { loading: true },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '读失败',
        note: '列表没读出来：错误替换列表那一格，不落进「暂无邀请码」。',
        props: { failed: true, errorDetail: '邀请码读不出来' },
        expect: '邀请码读不出来',
      },
    ],
  },
  {
    id: 'manage-topics-view',
    title: 'ManageTopicsView',
    about:
      '「分类标签」那一屏的画面：一列已经在用的标签（每行一颗移除），和一根「添加标签」打开的选择器弹窗。搜索与新建由容器通过 `searchTopics` / `createTopic` 两个回调传进来，画面在原时机调它们。读 store、真增删归容器 `ManageTopics.vue`。',
    file: 'src/views/spaces/detail/ManageTopicsView.vue',
    component: ManageTopicsView,
    needs: UI_T,
    args: {
      classificationTopics: [],
      selectedTopics: [],
      addTopicsDialog: false,
      ...TOPIC_CALLBACKS,
    },
    states: [
      {
        name: '还没有标签',
        note: '一个标签都没加：就地说一句「暂无标签」，并摆着「添加标签」那颗按钮。',
        props: {},
        expect: '暂无标签',
      },
      {
        name: '列着标签',
        note: '两个分类标签，各一行，右边一颗移除。',
        props: { classificationTopics: [topic(), topic({ id: 2, name: '图论' })] },
        expect: '动态规划',
      },
    ],
  },
  {
    id: 'topic-selector-view',
    title: 'TopicSelectorView',
    about:
      '分类标签选择器「画 + 交互」的那一半：一个可多选、可现造的标签输入框，搜什么、选了什么、要不要新建都在这里，真正的搜索与新建由 `searchTopics` / `createTopic` 两个回调给出（原件 `TopicSelector.vue` 自己连接口，这一半只认 props，于是场景里能单独画它）。',
    file: 'src/components/common/TopicSelectorView.vue',
    component: TopicSelectorView,
    needs: UI_T,
    args: { modelValue: [], max: -1, ...TOPIC_CALLBACKS },
    states: [
      {
        name: '还没选',
        note: '空的输入框：写着「添加标签」和「搜索已有标签或创建新标签」这一行提示。',
        props: {},
        expect: '添加标签',
      },
    ],
  },
]
