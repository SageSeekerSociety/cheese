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
    about:
      '一个成员点了空间管理、却被守卫挡下来时看到的一屏：说清「你没有管理权限」，出去的这一颗回题目列表，不是退出空间。',
    file: 'src/views/spaces/detail/ManageDeniedView.vue',
    component: ManageDeniedView,
    needs: UI_T_R,
    args: { spaceId: '7' },
    states: [
      {
        name: '被挡在管理区外',
        note: '形状和项目的「无权进入」同款（同一个 `AccessNotice`），只是话是这块板自己的；出口回题目列表 —— 人通常还是这里的成员。',
        props: {},
        expect: '你没有管理权限',
      },
    ],
  },
  {
    id: 'publish-task-view',
    title: 'PublishTaskView',
    about:
      '发题页：一页从上往下就是一道题（内容、参与、时间、分类与话题、更多设置），从文件里读出好几道时上面多一排题目列表，勾着的一起发出去，发布按钮写明发几道。',
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
      libraryTo: '/spaces/7/settings/materials',
      publishing: false,
      drafts: PUBLISH_DRAFT_ONE,
      teaching: {},
      teachingOwn: false,
    },
    states: [
      {
        name: '一次发一道',
        note: '默认这一格：一张发题表（题目内容在最上面，右上角挂着「从文件导入」「使用模板」），发布按钮是「发布」，按钮旁边那句写着「发布后需管理员审核」。',
        props: {},
        // 单道题才有「题目内容」那一块，它右上角那两颗动作按钮里有一颗是稳定的锚：装好
        // 了（`ready`）且不是从文件读出的多道（否则只留选题参数、不下这一块）才在。
        expectSelector: '[data-testid="publish-import"]',
      },
      {
        name: '一次发好几道',
        note: '从文件里读出了三道（勾了两道）：上面一排题目列表说清文件叫什么都读出了几道，发布按钮改成写明发几道。',
        props: { drafts: PUBLISH_DRAFT_MANY, importedFile: '第十章习题.pdf' },
        expect: '第十章习题.pdf · 识别出 3 道题，已选 2 道',
      },
      {
        name: '空间还没装好',
        note: '分类、域名组还挂在空间上、没到货：不挂表单，只留页头那一行，说明这道题发出去要过审 —— 发布那颗这时是禁用的。',
        props: { ready: false },
        expectSelector: '[data-testid="publish-submit"].v-btn--disabled',
      },
    ],
  },
  {
    id: 'basic-info-view',
    title: 'BasicInfoView',
    about:
      '空间设置「基本信息」这一栏：头像、名称、简介，管理员多一栏「题目可见上限」，所有者再看到危险区（删除空间）。读写都在容器里，这里只画和发事件。',
    file: 'src/views/spaces/detail/settings/BasicInfoView.vue',
    component: BasicInfoView,
    needs: UI_T,
    args: { space: SPACE_TASK_BOARD, isManager: true, isOwner: true, saving: false, deleting: false },
    states: [
      {
        name: '所有者兼管理员',
        note: '默认这一格：表单填着这块板的名称与简介，管理员看得到「题目可见上限」，最下面多一块仅创建者可见的危险区。',
        props: {},
        expect: '删除空间',
        expectSelector: 'section.danger',
      },
      {
        name: '只是普通成员',
        note: '既不是管理员也不是所有者：上限那一栏和危险区都不画 —— 摆一颗必然失败（403）的按钮比不摆更糟。',
        props: { isManager: false, isOwner: false },
        // 表单还是那一张，只是少了管理员/所有者才看得到的两栏：钉住真在的那一格「简介」。
        expectSelector: '#space-intro',
      },
    ],
  },
  {
    id: 'space-settings-index-view',
    title: 'SpaceSettingsIndexView',
    about:
      '空间设置那一层外框：桌面上左边灰底是目录（这是谁的设置）、右边白底是这一栏；手机上整屏，先目录再一栏。这一层有自己的地址，每一项是一条路由。',
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
        name: '选中了基本信息这一栏',
        note: '默认这一格：左边目录列出空间设置的七栏（当前是「基本信息」）、这一项高亮，右边摆的是它的内容。',
        props: {},
        // `active` 指到哪一项，目录里就哪一项高亮 —— 这一格钉的就是那一下。
        expectSelector: '.so__item.so__item--active',
      },
      {
        name: '还没选中哪一栏',
        note: '`active` 为空 = 还没有哪一栏被选中：窄屏上这就是「停在目录」（整屏目录、点一项才进去）。预览站这个视口是桌面宽度，看到的是桌面壳里目录一项都没高亮。',
        props: { active: null },
        expectSelector: '.so__item:not(.so__item--active)',
      },
    ],
  },
  {
    id: 'space-guidance-view',
    title: 'SpaceGuidanceView',
    about:
      '空间设置「给 AI 队友的指导」这一栏：一句说明 + 六格表单 + 保存。六格全空 = 这块板没设默认，下面哪一层说了就听哪一层。',
    file: 'src/views/spaces/detail/settings/SpaceGuidanceView.vue',
    component: SpaceGuidanceView,
    needs: UI_T,
    args: {
      teaching: SPACE_TEACHING,
      materials: SPACE_MATERIALS,
      materialsState: 'ready',
      libraryTo: '/spaces/7/settings/materials',
      saving: false,
    },
    states: [
      {
        name: '这块板设过默认',
        note: '默认这一格：系统提示、当前周次、本周范围都填着，考究设置收在「更多」里。',
        props: {},
        // 资料清单读到了（`ready`）：参考资料那一格才摆得出可勾的那一条。
        expectSelector: '[data-testid="teaching-materials-toggle"]',
      },
      {
        name: '还没设过默认',
        note: '六格全空就是「没说」：资料清单这次也没读出来，但读不出来不等于东西没了，不判失效 —— 那一格换成一句说明，不摆候选。',
        props: { teaching: undefined, materials: undefined, materialsState: 'error' },
        expectSelector: '[data-testid="teaching-materials-error"]',
      },
    ],
  },
  {
    id: 'space-materials-view',
    title: 'SpaceMaterialsView',
    about:
      '空间设置「资料库」这一栏：成员都能看，标了「仅管理员」的成员看不到、他们的 AI 也读不到。每一行能下载、改可见范围、撤下。',
    file: 'src/views/spaces/detail/settings/SpaceMaterialsView.vue',
    component: SpaceMaterialsView,
    needs: UI_T,
    args: { materials: SPACE_MATERIALS, canManage: true, loading: false, busy: false, uploadOpen: false },
    states: [
      {
        name: '有资料，管理员看得到全部',
        note: '默认这一格：三份资料一张清单，每一行写着类型、大小、上传日期、下载次数，还有引用它的地方数；账号能管，所以每一行都有改可见范围和撤下的入口。',
        props: {},
        expect: '实验指导书.pdf',
      },
      {
        name: '一份都还没有',
        note: '资料库空着：说一句「暂无资料」，不画一块空白。',
        props: { materials: [] },
        expect: '暂无资料',
      },
      {
        name: '清单还在读',
        note: '还没读到清单：原地转一个圈，不先说「暂无资料」——那会把「没读到」说成「没有」。',
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '上传表单开着',
        note: '按下「上传资料」后冒出来的表单：选一个文件、定可见范围（所有成员 / 仅管理员），两格都是必需的。',
        props: { uploadOpen: true },
        expect: '上传一份资料',
      },
    ],
  },
  {
    id: 'brief-view',
    title: 'BriefView',
    about:
      '题目详情「说明」页签：题目详情（富文本）和材料清单，点不点得动、下载几次都由服务端说。没有材料就不画那一段。',
    file: 'src/views/tasks/detail/BriefView.vue',
    component: BriefView,
    needs: UI_T,
    args: { taskData: TASK_DEMO, attachments: TASK_BRIEF_ATTACHMENTS, canDownload: true, downloadingId: null },
    states: [
      {
        name: '有说明也有材料',
        note: '默认这一格：上面是题目详情，下面「材料」一节列出两份可下载的文件。',
        props: {},
        expect: '材料',
        // 材料那一节紧跟在说明那一节后面 —— 两节都在，才配得上这个兄弟选择器。
        expectSelector: '.tb__sec + .tb__sec',
      },
      {
        name: '只有说明，没有材料',
        note: '这道题一份材料都没带：整段材料不画，不留一个空标题 —— 只剩说明那一节。',
        props: { attachments: [] },
        // 只剩一节时，它既是第一个也是最后一个：上一格那个兄弟选择器在这边匹配不到东西。
        expectSelector: '.tb__sec:first-child:last-child',
      },
    ],
  },
  {
    id: 'insights-view',
    title: 'InsightsView',
    about: '题目详情「数据」页签：这道题有几个人领、走到哪一步、卡在哪儿、什么时候动的。数字全从名单和提交算出来。',
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
        name: '有人领、有人交',
        note: '默认这一格：四张 KPI（领取人数 / 已提交 / 通过率 / 领了没动）、领取走势、走到哪一步、小队构成。',
        props: {},
        expect: '领取人数',
      },
      {
        name: '读失败不画成 0',
        note: '名单和提交都没读出来：说明读失败并给重试，而不是画一张「0 人领、通过率 0%」的看板装成没事。',
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '这道题的数据没读出来',
      },
      {
        name: '不是出题人也不是管理员',
        note: '无权看这一页的人被告知为什么，而不是看到一张空表。',
        props: { canManage: false },
        expect: '这一页只对这道题的出题人本人和管理员开放。',
      },
      {
        name: '找不到这道题',
        note: '题本身没读到：说一句「找不到这道题」，不画一屏空看板。',
        props: { task: null, loading: false },
        expect: '找不到这道题',
      },
    ],
  },
  {
    id: 'roster-view',
    title: 'RosterView',
    about:
      '题目详情「领取者」页签：批领取申请、看逐版提交、按状态筛，都在这一张表里。取数在容器里，这里只报「对谁做什么」。',
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
        name: '名单到货',
        note: '默认这一格：上面按状态筛的几个页签，下面一张表 —— 谁领的、什么时候领、走到哪一步、最新一版、截止，行尾是这一行能做的操作。',
        props: {},
        expect: '李雷',
      },
      {
        name: '名单还在读',
        note: '还没读到名单：原地写一句「加载中」，不先说「暂无领取者」。',
        props: { loading: true },
        expect: '加载中',
      },
      {
        name: '不给你看',
        note: '服务端按权限把关，403 是「不给你看」：写清只有出题人和空间管理员能看领取者。',
        props: { denied: true },
        expect: '只有这道题的出题人和空间管理员能看领取者',
      },
      {
        name: '这一次没读到',
        note: '和「不给你看」分开：读失败给的是失败和重试，不是权限那句话。',
        props: { failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '领取者名单没读出来',
      },
      {
        name: '还没有人领',
        note: '读过、名单确实空着：这时才说「暂无领取者」。',
        props: { participants: [] },
        expect: '暂无领取者',
      },
    ],
  },
]
