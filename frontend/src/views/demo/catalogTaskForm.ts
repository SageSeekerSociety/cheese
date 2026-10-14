/**
 * 发题表单（`components/tasks/TaskForm.vue`）拆出来的那几节在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行 —— 和 `catalogAccept.ts`、
 * `catalogQueue.ts` 同一个理由。夹具也写在这一份里（它们只给这些条目用）。
 *
 * 每一节都只吃 props、只往上发事件（A 级），所以每一节都能单独摆在这里：想看「团队题
 * 那几格」或者「更多设置收起时那一行」，不用先新建一道题。整张表也登记了一格
 * （`task-form`）：它是一层接线，同样只吃 props、只往上发事件。它不是场景，所以
 * `lint:scenes` 里没有它的格子。
 *
 * 实名那个弹窗独立（`teleport`）：它画在 body 上，和表单有什么关系是容器的事。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `TASK_FORM_ENTRIES`
 * 这个值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import TaskFormClassify from '@/components/tasks/form/TaskFormClassify.vue'
import TaskFormMore from '@/components/tasks/form/TaskFormMore.vue'
import TaskFormParticipation from '@/components/tasks/form/TaskFormParticipation.vue'
import TaskFormPrivacyDialog from '@/components/tasks/form/TaskFormPrivacyDialog.vue'
import TaskFormSection from '@/components/tasks/form/TaskFormSection.vue'
import TaskFormTime from '@/components/tasks/form/TaskFormTime.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

/** 只吃 vuetify 的那几件。 */
const UI: CatalogNeed[] = ['vuetify']
/** 还要翻句子的（标题、字段名、提示语）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/**
 * `defineField` 的另一半。真身是 vee-validate 按 `vuetifyConfig` 算出来的
 * `{ 'error-messages', error }`；`OK` 是不标红的那一份。
 */
const OK = { 'error-messages': [] as string[], error: false }
/** 标红的那一份：`error-messages` 非空就是它。 */
const red = (message: string) => ({ 'error-messages': [message], error: true })

const list = (title: string, value: number) => ({ title, value })

const TOPIC_ITEMS = [list('Web 安全', 1), list('逆向工程', 2), list('密码学', 3)]
const CATEGORY_ITEMS = [list('课程作业', 1), list('比赛题目', 2)]
const DOMAIN_GROUPS = [
  { title: '校内网络', value: 1, subtitle: 'ustc.edu.cn, mail.ustc.edu.cn' },
  { title: '合作单位', value: 2, subtitle: 'example.org' },
]

/** 「参与」那一节的一格。 */
const participation = (extra: Record<string, unknown> = {}) => ({
  submitterType: 'USER',
  rank: 2,
  participantLimit: null,
  participantLimitUnlimited: true,
  teamLockingPolicy: 'NO_LOCK',
  minTeamSize: 1,
  maxTeamSize: 10,
  isEditing: false,
  submitterTypeControl: OK,
  rankControl: OK,
  participantLimitControl: OK,
  minTeamSizeControl: OK,
  maxTeamSizeControl: OK,
  ...extra,
})

/** 「时间」那一节的一格。`deadline` 为空就是「不设截止」。 */
const time = (extra: Record<string, unknown> = {}) => ({
  registrationStartAt: null,
  deadline: new Date(2026, 9, 14),
  defaultDeadline: 30,
  registrationStartAtControl: OK,
  deadlineControl: OK,
  defaultDeadlineControl: OK,
  ...extra,
})

/** 「分类与话题」那一节的一格。 */
const classify = (extra: Record<string, unknown> = {}) => ({
  categoryItems: CATEGORY_ITEMS,
  topicItems: TOPIC_ITEMS,
  categoryId: 1,
  topics: [1],
  categoryIdControl: OK,
  ...extra,
})

/** 「更多设置」的一格。 */
const more = (extra: Record<string, unknown> = {}) => ({
  domainGroupItems: DOMAIN_GROUPS,
  accessControlEnabled: false,
  accessDomainGroupIds: [],
  requireRealName: false,
  teachingCustom: false,
  ...extra,
})

export const TASK_FORM_ENTRIES: CatalogEntry[] = [
  {
    id: 'task-form-section',
    title: 'TaskFormSection',
    about: '发题表单的一节：一行标题（右边可以放这一节自己的按钮），下面一栏字段，节与节之间一条细线。',
    file: 'src/components/tasks/form/TaskFormSection.vue',
    component: TaskFormSection,
    needs: UI,
    states: [
      {
        name: '一节',
        note: '和设置页同一个写法：白底上按间距和线分组，不再一节一张卡片。',
        props: { title: '时间' },
        slot: '这里是这一节的字段。',
        expect: '时间',
      },
    ],
  },
  {
    id: 'task-form-participation',
    title: 'TaskFormParticipation',
    about: '「参与」那一节：个人还是团队、难度、人数上限；团队题再加每队几人和报名通过后能不能换人。',
    file: 'src/components/tasks/form/TaskFormParticipation.vue',
    component: TaskFormParticipation,
    needs: UI_T,
    states: [
      {
        name: '新发一道个人题',
        note: '默认那一格：个人题没有每队几人和换人那两格；人数上限默认「不限」，框锁着。',
        props: participation(),
        expect: '参与方式',
      },
      {
        name: '改一道已有的题',
        note: '参与方式发布后不能再改，改题时只显示，不给选。',
        props: participation({ isEditing: true }),
        expect: '个人',
      },
      {
        name: '团队题',
        note: '选了团队才出现每队几人和「队伍成员变更」，人数上限换成队伍数上限。',
        props: participation({
          submitterType: 'TEAM',
          teamLockingPolicy: 'LOCK_ON_APPROVAL',
          minTeamSize: 3,
          maxTeamSize: 5,
        }),
        expect: '报名通过后锁定',
      },
      {
        name: '最多比最少还少（标红）',
        note: '跨字段的那条规矩，红字落在每队人数那一格下面。',
        props: participation({
          submitterType: 'TEAM',
          minTeamSize: 5,
          maxTeamSize: 2,
          maxTeamSizeControl: red('最多人数不能少于最少人数'),
        }),
        expect: '最多人数不能少于最少人数',
      },
    ],
  },
  {
    id: 'task-form-time',
    title: 'TaskFormTime',
    about: '「时间」那一节：报名开始与截止（都可空）、领取后几天内完成。',
    file: 'src/components/tasks/form/TaskFormTime.vue',
    component: TaskFormTime,
    needs: UI_T,
    states: [
      {
        name: '默认',
        note: '报名开始空着就是现在开始；截止空着就是不截止。',
        props: time(),
        expect: '完成期限',
      },
      {
        name: '完成期限没填（标红）',
        note: '完成期限是必填的：领取那一刻按它算这个人的截止时间。',
        props: time({ defaultDeadline: undefined, defaultDeadlineControl: red('未填写完成期限') }),
        expect: '未填写完成期限',
      },
    ],
  },
  {
    id: 'task-form-classify',
    title: 'TaskFormClassify',
    about: '「分类与话题」那一节：侧栏哪个分类下（必选），带哪些话题（可多选）。',
    file: 'src/components/tasks/form/TaskFormClassify.vue',
    component: TaskFormClassify,
    needs: UI_T,
    states: [
      {
        name: '选好了',
        note: '分类只能一个，话题可以几个。',
        props: classify(),
        expect: '分类与话题',
      },
      {
        name: '没选分类（标红）',
        note: '点过发布才标红，打开页面时不红。',
        props: classify({ categoryId: undefined, categoryIdControl: red('未选择分类') }),
        expect: '未选择分类',
      },
    ],
  },
  {
    id: 'task-form-more',
    title: 'TaskFormMore',
    about: '「更多设置」：可领取范围、实名、AI 指导。默认收起，收起时那一行写明现在设成了什么。',
    file: 'src/components/tasks/form/TaskFormMore.vue',
    component: TaskFormMore,
    needs: UI_T,
    states: [
      {
        name: '收起（全是默认）',
        note: '多数题目一样都不用改，所以收起；那一行不用点开就看得见现在是什么。',
        props: more(),
        expect: '全部成员可领取',
      },
      {
        name: '收起（限了邮箱、要实名、单独写了指导）',
        note: '限了哪几个邮箱域名组就写哪几个的名字。',
        props: more({
          accessControlEnabled: true,
          accessDomainGroupIds: [1],
          requireRealName: true,
          teachingCustom: true,
        }),
        expect: '仅限 校内网络',
      },
    ],
  },
  {
    id: 'task-attachment-picker',
    title: 'TaskAttachmentPicker',
    about: '这道题带的材料：已经挂上的那几份，加一个「添加文件」。传到哪里由页面决定。',
    file: 'src/components/tasks/TaskAttachmentPicker.vue',
    component: TaskAttachmentPicker,
    needs: UI_T,
    states: [
      {
        name: '还没有',
        note: '上限是接口报的那个数，页面问来给它；问不到就不写那句话。',
        props: { files: [], uploading: false, maxFileBytes: 100 * 1024 * 1024 },
        expect: '添加文件',
      },
      {
        name: '挂了两份、正在传第三份',
        note: '传的时候「添加文件」按住，免得同一份传两遍。',
        props: {
          files: [
            { id: 1, name: '实验指导书.pdf', size: 2_400_000 },
            { id: 2, name: 'starter.zip', size: 12_400_000 },
          ],
          uploading: true,
          maxFileBytes: null,
        },
        expect: '实验指导书.pdf',
      },
    ],
  },
  {
    id: 'task-form-privacy-dialog',
    title: 'TaskFormPrivacyDialog',
    about: '「实名信息隐私保护」那段说明：第一次要求实名再提交时弹出来，读完点「了解并接受」才真的交上去。',
    file: 'src/components/tasks/form/TaskFormPrivacyDialog.vue',
    component: TaskFormPrivacyDialog,
    needs: UI_T,
    teleport: true,
    states: [
      {
        name: '弹着',
        note: '它只画这一段说明，人点的是哪一颗往外报一声；要不要弹、弹完之后那份提交怎么办全在 `useTaskForm.ts` 里。',
        props: { open: true },
        expect: '实名信息隐私保护',
      },
    ],
  },
  {
    id: 'task-form',
    title: 'TaskForm',
    about: '整张发题 / 改题的表：几节接在 `useTaskForm.ts` 的字段上。按钮在页头，不在表里。',
    file: 'src/components/tasks/TaskForm.vue',
    component: TaskForm,
    needs: UI_T,
    states: [
      {
        name: '一次发好几道（只管共用的设置）',
        note: '逐道的名称和描述在外面填，这张表只画共用的那几节。题目内容那一节带编辑器，在发题页上看。',
        props: { classificationTopics: [], parametersOnly: true },
        expect: '参与方式',
      },
    ],
  },
]
