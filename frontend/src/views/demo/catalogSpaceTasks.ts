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

import { AGENT_TYPES } from './catalogSharedFixtures'
import {
  DOMAIN_GROUP,
  PUBLISH_DRAFTS,
  SPACE_CATEGORY,
  SPACE_MATERIAL_TEXT,
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
  TASK_VERSIONS,
} from './catalogSpaceTasksFixtures'

import TeachingFields from '@/components/common/TeachingFields.vue'
import { t } from '@/i18n'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import SpaceLibraryView from '@/views/spaces/detail/library/SpaceLibraryView.vue'
import ManageDeniedView from '@/views/spaces/detail/ManageDeniedView.vue'
import MyDraftsView from '@/views/spaces/detail/MyDraftsView.vue'
import PublishTaskView from '@/views/spaces/detail/PublishTaskView.vue'
import BasicInfoView from '@/views/spaces/detail/settings/BasicInfoView.vue'
import IndexView from '@/views/spaces/detail/settings/IndexView.vue'
import SpaceGuidanceView from '@/views/spaces/detail/settings/SpaceGuidanceView.vue'
import BriefView from '@/views/tasks/detail/BriefView.vue'
import InsightsView from '@/views/tasks/detail/InsightsView.vue'
import RosterView from '@/views/tasks/detail/RosterView.vue'
import VersionsView from '@/views/tasks/detail/VersionsView.vue'

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
      // 这道题指定的默认队友（#944）：发题页没有原始值，起手就是「继承」。
      defaultRole: '',
      agentTypes: AGENT_TYPES,
      agentTypesFailed: false,
      drafts: PUBLISH_DRAFTS,
      currentDraftId: 41,
      saveState: 'saved',
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
        props: { agentOpen: false },
        expectSelector: '[data-testid="publish-agent-open"]',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s6.name')
        },
        get note() {
          return t('catalog.publishTaskView.s6.note')
        },
        props: { publishError: '这道题的提交项指向了不存在的阶段（HTTP 400）' },
        expectSelector: '[data-testid="publish-failed"]',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s7.name')
        },
        get note() {
          return t('catalog.publishTaskView.s7.note')
        },
        props: {
          currentDraftId: 42,
          strip: {
            source: '第二单元实验与作业.pdf',
            flagged: true,
            publishing: false,
            note: {
              text: '已发布 1 道；《Lab 4 矩阵转置优化》发布失败：这道题的提交项指向了不存在的阶段（HTTP 400）',
              ok: false,
            },
            items: [
              { id: 41, name: 'Lab 3 缓存模拟器', writing: false, missing: [], published: true },
              { id: 42, name: 'Lab 4 矩阵转置优化', writing: false, missing: [], published: false },
            ],
          },
        },
        expectSelector: '[data-testid="draft-strip"]',
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
    id: 'teaching-fields',
    title: 'TeachingFields',
    about:
      '「AI 队友辅导」那几格，空间设置和发题页、改题页共用。题目那一层多一张「沿用上层设置」，底下折着空间那一份要求。',
    file: 'src/components/common/TeachingFields.vue',
    component: TeachingFields,
    needs: UI_T,
    args: { materials: SPACE_MATERIALS, materialsState: 'ready' },
    states: [
      {
        name: '题目那一层，沿用空间的方式',
        note: '没选方式就用空间的「引导为主」；本题补充要求只写这道题自己的，空间那一份折在最下面。',
        props: {
          inherit: true,
          spaceTeaching: SPACE_TEACHING,
          modelValue: {
            mode: null,
            systemPrompt: '这道题考调度算法。学生问到时间片轮转时，先让他画出甘特图，再讨论时间片大小。',
            materialIds: [],
            documentIds: [],
          },
        },
        expectSelector: '[data-testid="teaching-space-prompt"]',
      },
      {
        name: '空间那一层',
        note: '四种方式，没有「沿用」；选中的那张描深色边。',
        props: { modelValue: SPACE_TEACHING },
        expect: '引导为主',
      },
    ],
  },
  {
    id: 'space-library-view',
    title: 'SpaceLibraryView',
    get about() {
      return t('catalog.spaceLibraryView.about')
    },
    file: 'src/views/spaces/detail/library/SpaceLibraryView.vue',
    component: SpaceLibraryView,
    needs: UI_T,
    args: {
      materials: SPACE_MATERIALS,
      canManage: true,
      loading: false,
      failed: false,
      error: null,
      selected: null,
      wide: true,
      busy: false,
      uploadOpen: false,
      read: () => async () => new TextEncoder().encode(SPACE_MATERIAL_TEXT).buffer,
    },
    states: [
      {
        get name() {
          return t('catalog.spaceLibraryView.s1.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s1.note')
        },
        props: {},
        expect: '实验指导书.pdf',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s2.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s2.note')
        },
        props: { canManage: false, materials: SPACE_MATERIALS.filter((m) => m.visibility === 'members') },
        expect: '实验指导书.pdf',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s3.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s3.note')
        },
        props: { selected: SPACE_MATERIALS[0] },
        expectSelector: '[data-testid="slib-detail"]',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s4.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s4.note')
        },
        props: { materials: [] },
        expect: '暂无资料',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s5.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s5.note')
        },
        props: { materials: [], loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s6.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s6.note')
        },
        props: { selected: SPACE_MATERIALS[0], wide: false },
        expectSelector: '[data-testid="slib-detail"]',
      },
      {
        get name() {
          return t('catalog.spaceLibraryView.s7.name')
        },
        get note() {
          return t('catalog.spaceLibraryView.s7.note')
        },
        props: { uploadOpen: true },
        // The dialog teleports out of the frame's root; the list behind it is what mounts here.
        expect: '实验指导书.pdf',
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
    args: { taskData: TASK_DEMO, attachments: TASK_BRIEF_ATTACHMENTS, canDownload: true, downloading: null },
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
    id: 'versions-view',
    title: 'VersionsView',
    get about() {
      return t('catalog.versionsView.about')
    },
    file: 'src/views/tasks/detail/VersionsView.vue',
    component: VersionsView,
    needs: UI_T,
    args: { versions: TASK_VERSIONS, canDownload: true },
    states: [
      {
        get name() {
          return t('catalog.versionsView.s1.name')
        },
        get note() {
          return t('catalog.versionsView.s1.note')
        },
        props: {},
        expect: '第 3 版',
      },
      {
        get name() {
          return t('catalog.versionsView.s2.name')
        },
        get note() {
          return t('catalog.versionsView.s2.note')
        },
        props: { focus: 2 },
        expectSelector: '.tv__item--focus',
      },
      {
        get name() {
          return t('catalog.versionsView.s3.name')
        },
        get note() {
          return t('catalog.versionsView.s3.note')
        },
        props: { canDownload: false },
        expect: '开始做这道题之后才能下载',
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
        expect: '参与人数',
      },
      {
        get name() {
          return t('catalog.insightsView.s2.name')
        },
        get note() {
          return t('catalog.insightsView.s2.note')
        },
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '这道题的数据加载失败',
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
        expect: '只有这道题的出题人和空间管理员能看参与者',
      },
      {
        get name() {
          return t('catalog.rosterView.s4.name')
        },
        get note() {
          return t('catalog.rosterView.s4.note')
        },
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '参与者名单加载失败',
      },
      {
        get name() {
          return t('catalog.rosterView.s5.name')
        },
        get note() {
          return t('catalog.rosterView.s5.note')
        },
        props: { participants: [] },
        expect: '暂无参与者',
      },
    ],
  },
]
