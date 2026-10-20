/**
 * 预览站里「空间与题目那一组整页」：空间设置的四栏、发题页、被拒进门的那一屏，和题目
 * 详情下的三个页签（说明、数据、领取者）。
 *
 * 这些页面在应用里都由各自的容器取数（`SpacesDetail*` / `tasks/detail/*` 那一批），
 * 容器碰接口层，预览站碰不得。好在它们都已经拆成「容器 → 只吃 props 的画面」：这里摆的
 * 正是产品里真会走到的那几格 —— 数据到货 / 首次加载 / 读失败 / 空 / 某一栏选中。
 * 各屏公用的一份数据见 `catalogSpaceTasksFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的
 * `CatalogEntry`、`CatalogNeed` 是 type-only 引用：`catalog.ts` 反过来要分册的值，
 * 运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  DOMAIN_GROUP,
  PANEL_DONE,
  PANEL_LIVE,
  PANEL_SPLIT,
  PUBLISH_DRAFTS,
  SPACE_CATEGORY,
  SPACE_MATERIALS,
  SPACE_SETTINGS_GROUPS,
  SPACE_TASK_BOARD,
  SPACE_TEACHING,
  SPACE_TEMPLATES,
  TASK_BRIEF_ATTACHMENTS,
  TASK_DEMO,
  TASK_LATEST,
  TASK_REVIEWS,
  TASK_ROSTER,
} from './catalogSpaceTasksFixtures'

import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import { t } from '@/i18n'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import ManageDeniedView from '@/views/spaces/detail/ManageDeniedView.vue'
import MyDraftsView from '@/views/spaces/detail/MyDraftsView.vue'
import PublishTaskView from '@/views/spaces/detail/PublishTaskView.vue'
import BasicInfoView from '@/views/spaces/detail/settings/BasicInfoView.vue'
import IndexView from '@/views/spaces/detail/settings/IndexView.vue'
import SpaceGuidanceView from '@/views/spaces/detail/settings/SpaceGuidanceView.vue'
import SpaceMaterialsView from '@/views/spaces/detail/settings/SpaceMaterialsView.vue'
import BriefView from '@/views/tasks/detail/BriefView.vue'
import InsightsView from '@/views/tasks/detail/InsightsView.vue'
import RosterView from '@/views/tasks/detail/RosterView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面那些，外加一条 name 指得上的路由（按钮、链接按名字指路时用）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_SPACE_TASKS_ENTRIES: CatalogEntry[] = [
  {
    id: 'manage-denied-view',
    title: 'ManageDeniedView',
    get about() {
      return t('catalog.manageDeniedView.about')
    },
    file: 'src/views/spaces/detail/ManageDeniedView.vue',
    component: ManageDeniedView,
    needs: UI_T_R,
    args: { spaceId: '7' },
    states: [
      {
        get name() {
          return t('catalog.manageDeniedView.s1.name')
        },
        get note() {
          return t('catalog.manageDeniedView.s1.note')
        },
        props: {},
        expect: '你没有管理权限',
      },
    ],
  },
  {
    id: 'assistant-panel',
    title: 'AssistantPanel',
    get about() {
      return t('catalog.assistantPanel.about')
    },
    file: 'src/components/assistant/AssistantPanel.vue',
    component: AssistantPanel,
    needs: UI_T_R,
    args: {
      conversations: [],
      currentId: null,
      title: '',
      messages: [],
      streaming: null,
      tool: null,
      notice: null,
      busy: false,
      context: '',
      get greeting() {
        return t('tasks.assistant.draft.greeting')
      },
      get starters() {
        return [t('tasks.assistant.draft.starters.rubric'), t('tasks.assistant.draft.starters.check')]
      },
      get placeholder() {
        return t('tasks.assistant.draft.placeholder')
      },
      get hint() {
        return t('tasks.assistant.draft.hint')
      },
      canAttach: true,
    },
    states: [
      {
        get name() {
          return t('catalog.assistantPanel.s1.name')
        },
        get note() {
          return t('catalog.assistantPanel.s1.note')
        },
        props: {},
        expect: '起草提交内容和评分要点',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s2.name')
        },
        get note() {
          return t('catalog.assistantPanel.s2.note')
        },
        props: { ...PANEL_LIVE, busy: true, streaming: '' },
        expect: '填写题目',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s3.name')
        },
        get note() {
          return t('catalog.assistantPanel.s3.note')
        },
        props: PANEL_DONE,
        expect: '待你确认',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s4.name')
        },
        get note() {
          return t('catalog.assistantPanel.s4.note')
        },
        props: PANEL_SPLIT,
        expectSelector: '.at__card--one',
      },
    ],
  },
  {
    id: 'publish-task-view',
    // 芝士的面板停在页面外框的右边（导航抽屉），外框要有。
    layout: true,
    title: 'PublishTaskView',
    get about() {
      return t('catalog.publishTaskView.about')
    },
    file: 'src/views/spaces/detail/PublishTaskView.vue',
    component: PublishTaskView,
    needs: UI_T,
    args: {
      ready: true,
      listTo: { name: 'SpacesDetailTasksList', params: { spaceId: '7' } },
      formKey: 1,
      initialData: {},
      classificationTopics: SPACE_TASK_BOARD.classificationTopics,
      categories: [SPACE_CATEGORY],
      selectedCategoryId: SPACE_CATEGORY.id,
      domainGroups: [DOMAIN_GROUP],
      templates: SPACE_TEMPLATES,
      attachments: [],
      uploading: false,
      maxFileBytes: 20_000_000,
      materials: SPACE_MATERIALS,
      materialsState: 'ready',
      libraryTo: spaceLibraryPath('7'),
      publishing: false,
      teaching: {},
      teachingOwn: false,
      drafts: PUBLISH_DRAFTS,
      currentDraftId: 41,
      saveState: 'saved',
      pointer: null,
      outline: null,
      marks: [],
      agentName: t('work.agentAvatar.defaultName'),
    },
    states: [
      {
        get name() {
          return t('catalog.publishTaskView.s1.name')
        },
        get note() {
          return t('catalog.publishTaskView.s1.note')
        },
        props: {},
        expect: '草稿已保存',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s2.name')
        },
        get note() {
          return t('catalog.publishTaskView.s2.note')
        },
        // 芝士正在写名称：带名字的文字光标停在字后面，那一格描着边；描述已经填过。
        props: {
          saveState: 'saving',
          pointer: { x: 180, y: 120, mode: 'caret', height: 22 },
          outline: { x: 12, y: 96, w: 600, h: 48 },
          marks: [{ key: 'description', label: t('tasks.form.description'), x: 620, y: 160 }],
        },
        expectSelector: '[data-testid="agent-mark-description"]',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s3.name')
        },
        get note() {
          return t('catalog.publishTaskView.s3.note')
        },
        props: { ready: false },
        expectSelector: '[data-testid="publish-submit"].v-btn--disabled',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s4.name')
        },
        get note() {
          return t('catalog.publishTaskView.s4.note')
        },
        props: {
          currentDraftId: 41,
          strip: {
            source: '第二单元实验与作业.pdf',
            flagged: true,
            publishing: false,
            note: null,
            items: [
              { id: 41, name: 'Lab 3 缓存模拟器', writing: false, missing: [], published: true },
              { id: 42, name: 'Lab 4 矩阵转置优化', writing: false, missing: ['难度'], published: false },
              { id: 43, name: '作业 6 虚拟内存', writing: true, missing: [], published: false },
            ],
          },
        },
        expectSelector: '[data-testid="draft-strip"]',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s5.name')
        },
        get note() {
          return t('catalog.publishTaskView.s5.note')
        },
        props: { agentDocked: false },
        expectSelector: '[data-testid="publish-agent-dock"]',
      },
    ],
  },
  {
    id: 'my-drafts-view',
    title: 'MyDraftsView',
    get about() {
      return t('catalog.myDraftsView.about')
    },
    file: 'src/views/spaces/detail/MyDraftsView.vue',
    component: MyDraftsView,
    needs: UI_T_R,
    args: {
      drafts: PUBLISH_DRAFTS,
      loading: false,
      failed: false,
      removing: false,
      publishTo: '/spaces/7/tasks/publish',
    },
    states: [
      {
        get name() {
          return t('catalog.myDraftsView.s1.name')
        },
        get note() {
          return t('catalog.myDraftsView.s1.note')
        },
        props: {},
        expect: 'Lab 4 矩阵转置优化',
      },
      {
        get name() {
          return t('catalog.myDraftsView.s2.name')
        },
        get note() {
          return t('catalog.myDraftsView.s2.note')
        },
        props: { drafts: [] },
        expect: '暂无草稿',
      },
    ],
  },
  {
    id: 'basic-info-view',
    title: 'BasicInfoView',
    get about() {
      return t('catalog.basicInfoView.about')
    },
    file: 'src/views/spaces/detail/settings/BasicInfoView.vue',
    component: BasicInfoView,
    needs: UI_T,
    args: { space: SPACE_TASK_BOARD, isManager: true, isOwner: true, saving: false, deleting: false },
    states: [
      {
        get name() {
          return t('catalog.basicInfoView.s1.name')
        },
        get note() {
          return t('catalog.basicInfoView.s1.note')
        },
        props: {},
        expect: '删除空间',
        expectSelector: 'section.danger',
      },
      {
        get name() {
          return t('catalog.basicInfoView.s2.name')
        },
        get note() {
          return t('catalog.basicInfoView.s2.note')
        },
        props: { isManager: false, isOwner: false },
        // 表单还是那一张，只是少了管理员/所有者才看得到的两栏：钉住真在的那一格「简介」。
        expectSelector: '#space-intro',
      },
    ],
  },
  {
    id: 'space-settings-index-view',
    title: 'SpaceSettingsIndexView',
    get about() {
      return t('catalog.spaceSettingsIndexView.about')
    },
    file: 'src/views/spaces/detail/settings/IndexView.vue',
    component: IndexView,
    needs: UI_T_R,
    teleport: true,
    args: {
      spaceName: '操作系统 2026 秋',
      avatar: '',
      groups: SPACE_SETTINGS_GROUPS,
      active: 'SpacesDetailSettingsBasic',
      title: t('spaces.settings.tabs.basic'),
      backTo: { name: 'SpacesDetailTasksList', params: { spaceId: '7' } },
    },
    states: [
      {
        get name() {
          return t('catalog.spaceSettingsIndexView.s1.name')
        },
        get note() {
          return t('catalog.spaceSettingsIndexView.s1.note')
        },
        props: {},
        // `active` 指到哪一项，目录里就哪一项高亮 —— 这一格钉的就是那一下。
        expectSelector: '.so__item.so__item--active',
      },
      {
        get name() {
          return t('catalog.spaceSettingsIndexView.s2.name')
        },
        get note() {
          return t('catalog.spaceSettingsIndexView.s2.note')
        },
        props: { active: null },
        // `active` 为空时目录里一项都不高亮：默认那一格（s1）点亮的是首项（Basic），这里钉
        // 首项**没有** active 类，两格互斥。happy-dom 认不了 `:has()`，「一个 active 都没有」
        // 这种「缺席」用 expectSelector 表达不出来，只能落到具体那一项上。
        expectSelector: '.so__item:first-of-type:not(.so__item--active)',
      },
    ],
  },
  {
    id: 'space-guidance-view',
    title: 'SpaceGuidanceView',
    get about() {
      return t('catalog.spaceGuidanceView.about')
    },
    file: 'src/views/spaces/detail/settings/SpaceGuidanceView.vue',
    component: SpaceGuidanceView,
    needs: UI_T,
    args: {
      teaching: SPACE_TEACHING,
      materials: SPACE_MATERIALS,
      materialsState: 'ready',
      libraryTo: spaceLibraryPath('7'),
      saving: false,
    },
    states: [
      {
        get name() {
          return t('catalog.spaceGuidanceView.s1.name')
        },
        get note() {
          return t('catalog.spaceGuidanceView.s1.note')
        },
        props: {},
        // 资料清单读到了（`ready`）：参考资料那一格才摆得出可勾的那一条。
        expectSelector: '[data-testid="teaching-materials-toggle"]',
      },
      {
        get name() {
          return t('catalog.spaceGuidanceView.s2.name')
        },
        get note() {
          return t('catalog.spaceGuidanceView.s2.note')
        },
        props: { teaching: undefined, materials: undefined, materialsState: 'error' },
        expectSelector: '[data-testid="teaching-materials-error"]',
      },
    ],
  },
  {
    id: 'space-materials-view',
    title: 'SpaceMaterialsView',
    get about() {
      return t('catalog.spaceMaterialsView.about')
    },
    file: 'src/views/spaces/detail/settings/SpaceMaterialsView.vue',
    component: SpaceMaterialsView,
    needs: UI_T,
    args: { materials: SPACE_MATERIALS, canManage: true, loading: false, busy: false, uploadOpen: false },
    states: [
      {
        get name() {
          return t('catalog.spaceMaterialsView.s1.name')
        },
        get note() {
          return t('catalog.spaceMaterialsView.s1.note')
        },
        props: {},
        expect: '实验指导书.pdf',
      },
      {
        get name() {
          return t('catalog.spaceMaterialsView.s2.name')
        },
        get note() {
          return t('catalog.spaceMaterialsView.s2.note')
        },
        props: { materials: [] },
        expect: '暂无资料',
      },
      {
        get name() {
          return t('catalog.spaceMaterialsView.s3.name')
        },
        get note() {
          return t('catalog.spaceMaterialsView.s3.note')
        },
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.spaceMaterialsView.s4.name')
        },
        get note() {
          return t('catalog.spaceMaterialsView.s4.note')
        },
        props: { uploadOpen: true },
        expect: '上传一份资料',
      },
    ],
  },
  {
    id: 'brief-view',
    title: 'BriefView',
    get about() {
      return t('catalog.briefView.about')
    },
    file: 'src/views/tasks/detail/BriefView.vue',
    component: BriefView,
    needs: UI_T,
    args: { taskData: TASK_DEMO, attachments: TASK_BRIEF_ATTACHMENTS, canDownload: true, downloadingId: null },
    states: [
      {
        get name() {
          return t('catalog.briefView.s1.name')
        },
        get note() {
          return t('catalog.briefView.s1.note')
        },
        props: {},
        expect: '材料',
        // 材料那一节紧跟在说明那一节后面 —— 两节都在，才配得上这个兄弟选择器。
        expectSelector: '.tb__sec + .tb__sec',
      },
      {
        get name() {
          return t('catalog.briefView.s2.name')
        },
        get note() {
          return t('catalog.briefView.s2.note')
        },
        props: { attachments: [], taskData: { ...TASK_DEMO, submissionSchema: [] } },
        // 只剩一节时，它既是第一个也是最后一个：上一格那个兄弟选择器在这边匹配不到东西。
        expectSelector: '.tb__sec:first-child:last-child',
      },
      {
        get name() {
          return t('catalog.briefView.s3.name')
        },
        get note() {
          return t('catalog.briefView.s3.note')
        },
        props: {
          taskData: {
            ...TASK_DEMO,
            rubricPublic: false,
            rubric: [
              { item: '正确性', points: 60, criteria: '样例全部通过，调度顺序与参考输出一致' },
              { item: '报告', points: 40, criteria: '说明优先级反转如何避免' },
            ],
          },
        },
        expect: '仅批改的人可见',
      },
    ],
  },
  {
    id: 'insights-view',
    title: 'InsightsView',
    get about() {
      return t('catalog.insightsView.about')
    },
    file: 'src/views/tasks/detail/InsightsView.vue',
    component: InsightsView,
    needs: UI_T,
    args: {
      task: TASK_DEMO,
      roster: TASK_ROSTER,
      reviewByParticipant: TASK_REVIEWS,
      canManage: true,
      loading: false,
      failed: false,
      failureReason: null,
      forbidden: false,
    },
    states: [
      {
        get name() {
          return t('catalog.insightsView.s1.name')
        },
        get note() {
          return t('catalog.insightsView.s1.note')
        },
        props: {},
        expect: '领取人数',
      },
      {
        get name() {
          return t('catalog.insightsView.s2.name')
        },
        get note() {
          return t('catalog.insightsView.s2.note')
        },
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '这道题的数据没读出来',
      },
      {
        get name() {
          return t('catalog.insightsView.s3.name')
        },
        get note() {
          return t('catalog.insightsView.s3.note')
        },
        props: { canManage: false },
        expect: '这一页只对这道题的出题人本人和管理员开放。',
      },
      {
        get name() {
          return t('catalog.insightsView.s4.name')
        },
        get note() {
          return t('catalog.insightsView.s4.note')
        },
        props: { task: null, loading: false },
        expect: '找不到这道题',
      },
    ],
  },
  {
    id: 'roster-view',
    title: 'RosterView',
    get about() {
      return t('catalog.rosterView.about')
    },
    file: 'src/views/tasks/detail/RosterView.vue',
    component: RosterView,
    needs: UI_T,
    args: {
      taskData: TASK_DEMO,
      participants: TASK_ROSTER,
      latestByParticipant: TASK_LATEST,
      loading: false,
      denied: false,
      failed: false,
      failureReason: null,
      busyId: null,
    },
    states: [
      {
        get name() {
          return t('catalog.rosterView.s1.name')
        },
        get note() {
          return t('catalog.rosterView.s1.note')
        },
        props: {},
        expect: '李雷',
      },
      {
        get name() {
          return t('catalog.rosterView.s2.name')
        },
        get note() {
          return t('catalog.rosterView.s2.note')
        },
        props: { loading: true },
        expect: '加载中',
      },
      {
        get name() {
          return t('catalog.rosterView.s3.name')
        },
        get note() {
          return t('catalog.rosterView.s3.note')
        },
        props: { denied: true },
        expect: '只有这道题的出题人和空间管理员能看领取者',
      },
      {
        get name() {
          return t('catalog.rosterView.s4.name')
        },
        get note() {
          return t('catalog.rosterView.s4.note')
        },
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '领取者名单没读出来',
      },
      {
        get name() {
          return t('catalog.rosterView.s5.name')
        },
        get note() {
          return t('catalog.rosterView.s5.note')
        },
        props: { participants: [] },
        expect: '暂无领取者',
      },
    ],
  },
]
