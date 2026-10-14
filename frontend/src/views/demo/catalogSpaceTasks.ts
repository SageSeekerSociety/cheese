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
  PUBLISH_DRAFT_MANY,
  PUBLISH_DRAFT_ONE,
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

import { t } from '@/i18n'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import ManageDeniedView from '@/views/spaces/detail/ManageDeniedView.vue'
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
    id: 'publish-task-view',
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
      importing: false,
      importedFile: null,
      attachments: [],
      uploading: false,
      maxFileBytes: 20_000_000,
      materials: SPACE_MATERIALS,
      materialsState: 'ready',
      libraryTo: spaceLibraryPath('7'),
      publishing: false,
      drafts: PUBLISH_DRAFT_ONE,
      teaching: {},
      teachingOwn: false,
      // 这道题指定的默认队友（#944）：发题页没有原始值，起手就是「继承」。
      defaultRole: '',
      agentTypes: AGENT_TYPES,
      agentTypesFailed: false,
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
        // 单道题才有「题目内容」那一块，它右上角那两颗动作按钮里有一颗是稳定的锚：装好
        // 了（`ready`）且不是从文件读出的多道（否则只留选题参数、不下这一块）才在。
        expectSelector: '[data-testid="publish-import"]',
      },
      {
        get name() {
          return t('catalog.publishTaskView.s2.name')
        },
        get note() {
          return t('catalog.publishTaskView.s2.note')
        },
        props: { drafts: PUBLISH_DRAFT_MANY, importedFile: '第十章习题.pdf' },
        expect: '第十章习题.pdf · 识别出 3 道题，已选 2 道',
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
        props: { attachments: [] },
        // 只剩一节时，它既是第一个也是最后一个：上一格那个兄弟选择器在这边匹配不到东西。
        expectSelector: '.tb__sec:first-child:last-child',
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
