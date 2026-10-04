/**
 * 发题表单（`components/tasks/TaskForm.vue`）拆出来的那几件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，十条塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogAccept.ts`、`catalogQueue.ts` 同一个
 * 理由。夹具也写在这一份里（它们只给这些条目用）。
 *
 * 为什么这些件值得一站：拆之前它们是 1089 行 `TaskForm.vue` 里的七段模板加两个弹窗
 * （#2143），想看其中任何一段都得先把整张表拉起来 —— 而整张表读 `inject` 进来的那份
 * 清单、还要有一个能提交的父级。拆开之后每一件都只吃 props、只往上发事件（A 级），
 * 于是每一件都能单独摆在预览站里：想看「勾了『不限』之后那个数长什么样」，不用先
 * 新建一道题。
 *
 * 整张表也在这里登记了一格（`task-form`）：它是一层接线（`v-model:<字段>` 对上
 * `useTaskForm.ts`），但这一层只吃 props、只往上发事件，按 `frontend_grade.py` 的口径
 * 是 A 级 —— 读 `inject` 那一手在 composable 里，而且是可选的
 * （`inject(PUBLISH_CHECKS_SINK, null)`，没人 provide 也照跑）。它不是场景：场景是路由
 * 到得了的页、`components/panels/` 下的一件、或者页的 `<Page>View.vue`，这一件三样
 * 都不是，所以 `lint:scenes` 里没有它的格子。
 *
 * 列表里那两个弹窗也各自独立（`teleport`）：它们画在 body 上，和表单有什么关系是
 * 容器的事。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `TASK_FORM_ENTRIES`
 * 这个值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import TaskFormAccessCard from '@/components/tasks/form/TaskFormAccessCard.vue'
import TaskFormBasicCard from '@/components/tasks/form/TaskFormBasicCard.vue'
import TaskFormClassifyCard from '@/components/tasks/form/TaskFormClassifyCard.vue'
import TaskFormDescriptionCard from '@/components/tasks/form/TaskFormDescriptionCard.vue'
import TaskFormPrivacyDialog from '@/components/tasks/form/TaskFormPrivacyDialog.vue'
import TaskFormRealNameCard from '@/components/tasks/form/TaskFormRealNameCard.vue'
import TaskFormSection from '@/components/tasks/form/TaskFormSection.vue'
import TaskFormTimeCard from '@/components/tasks/form/TaskFormTimeCard.vue'
import TaskFormVideoCard from '@/components/tasks/form/TaskFormVideoCard.vue'
import TaskFormVideoDialog from '@/components/tasks/form/TaskFormVideoDialog.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

/** 只吃 vuetify 的那几件（卡片、图标、按钮，一个字都不用翻）。 */
const UI: CatalogNeed[] = ['vuetify']
/** 还要翻句子的（卡片标题、字段名、提示语）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/**
 * `defineField` 的另一半 —— `v-bind` 到控件上的那一样。
 *
 * 真身是 vee-validate 按 `vuetifyConfig` 算出来的 `{ onBlur, onChange, onInput,
 * 'error-messages', error }`；这里只写看得见的两个，因为卡片对它是黑盒（整个对象
 * 原样 `v-bind`）。`OK` 是不标红的那一份。
 */
const OK = { 'error-messages': [] as string[], error: false }
/** 标红的那一份：`error-messages` 非空就是它。 */
const red = (message: string) => ({ 'error-messages': [message], error: true })

/** 只发参数那条路（`parametersOnly`）上，这两张卡整个不画。 */
const PARAMS_ONLY = { parametersOnly: true }

const list = (title: string, value: number) => ({ title, value })

const TOPIC_ITEMS = [list('Web 安全', 1), list('逆向工程', 2), list('密码学', 3)]
const CATEGORY_ITEMS = [list('课程作业', 1), list('比赛题目', 2)]
const DOMAIN_GROUPS = [
  { title: '校内网络', value: 1, subtitle: 'ustc.edu.cn, mail.ustc.edu.cn' },
  { title: '合作单位', value: 2, subtitle: 'example.org' },
]

/**
 * 基本信息那张卡的默认一格。
 *
 * `isEditing` / `parametersOnly` 是可选的（缺省就是 `false`），这里写全是为了让每
 * 一格读起来就是「这张卡在这件事发生时的样子」。
 */
const basic = (extra: Record<string, unknown> = {}) => ({
  name: '',
  submitterType: 'USER',
  rank: 1,
  participantLimit: null,
  participantLimitUnlimited: false,
  teamLockingPolicy: 'NO_LOCK',
  minTeamSize: 1,
  maxTeamSize: 10,
  isEditing: false,
  parametersOnly: false,
  nameControl: OK,
  submitterTypeControl: OK,
  rankControl: OK,
  participantLimitControl: OK,
  teamLockingPolicyControl: OK,
  minTeamSizeControl: OK,
  maxTeamSizeControl: OK,
  ...extra,
})

/** 时间设置那张卡的一格。`deadline` 为空就是「不设截止」。 */
const time = (extra: Record<string, unknown> = {}) => ({
  registrationStartAt: null,
  deadline: new Date(2026, 9, 14),
  defaultDeadline: 30,
  registrationStartAtControl: OK,
  deadlineControl: OK,
  defaultDeadlineControl: OK,
  ...extra,
})

/** 分类标签那张卡的一格。 */
const classify = (extra: Record<string, unknown> = {}) => ({
  categoryItems: CATEGORY_ITEMS,
  topicItems: TOPIC_ITEMS,
  categoryId: 1,
  topics: [1],
  categoryIdControl: OK,
  topicsControl: OK,
  ...extra,
})

export const TASK_FORM_ENTRIES: CatalogEntry[] = [
  {
    id: 'task-form-section',
    title: 'TaskFormSection',
    about: '发题表单每张卡的那个壳：左上角圆底图标、一行标题、底下一栏内容。',
    file: 'src/components/tasks/form/TaskFormSection.vue',
    component: TaskFormSection,
    needs: UI,
    states: [
      {
        name: '一张卡',
        note: '七张卡共用这一个壳（图标和标题都是 props）：改一遍就够了，改出来的样子也是同一份。',
        props: { icon: 'mdi-information-outline', title: '基本信息' },
        slot: '这里是这一张卡的内容。',
        expect: '基本信息',
      },
      {
        name: '长标题',
        note: '标题多长都照排，壳不截断也不缩字号 —— 标题是被读的，缩了就等于没说。',
        props: { icon: 'mdi-shield-account', title: '实名信息要求（这一行只是要长一点）' },
        slot: '内容',
        expect: '实名信息要求（这一行只是要长一点）',
      },
    ],
  },
  {
    id: 'task-form-basic-card',
    title: 'TaskFormBasicCard',
    about: '基本信息那张卡：题名、参与身份、题目等级、人数 / 队伍上限，和选了「队伍」之后的队伍那几栏。',
    file: 'src/components/tasks/form/TaskFormBasicCard.vue',
    component: TaskFormBasicCard,
    needs: UI_T,
    states: [
      {
        name: '新发一道个人题',
        note: '默认那一格：参与身份是个人，所以队伍那几栏（锁定策略、最小 / 最大人数）整块不画 —— 它们只在团队时算数。',
        props: basic(),
        expect: '基本信息',
      },
      {
        name: '改一道已有的题（题名和身份都锁着）',
        note: '改题时题名和参与身份都锁着：参与身份一改就是另一道题了。题名在输入框里（`textContent` 看不到输入框的值），所以机械验收按标题判它画出来了。',
        props: basic({ isEditing: true, name: '用 gdb 定位一次段错误' }),
        expect: '基本信息',
      },
      {
        name: '团队题（队伍那几栏出来了）',
        note: '选了团队才出现锁定策略和最小 / 最大人数；底下那段话说明白「参与记录只认审核通过时的那份名单」。',
        props: basic({
          submitterType: 'TEAM',
          teamLockingPolicy: 'LOCK_ON_APPROVAL',
          minTeamSize: 3,
          maxTeamSize: 5,
        }),
        expect: '审核通过后锁定',
      },
      {
        name: '最大比最小还小（标红）',
        note: '这条规矩在 schema 里是跨字段的 `refine`，所以红字落在「最大队伍人数」那一栏 —— 卡片只是把 `defineField` 给的那一半原样 `v-bind` 上去。',
        props: basic({
          submitterType: 'TEAM',
          minTeamSize: 5,
          maxTeamSize: 2,
          maxTeamSizeControl: red('最大人数不能小于最小人数'),
        }),
        expect: '最大人数不能小于最小人数',
      },
      {
        name: '上限「不限」（勾上）',
        note: '勾上「不限」不是把那个数改成 0，而是把它放回「没填过」：框锁上、值清掉，交出去的 payload 与从头没填过一模一样。',
        props: basic({ participantLimitUnlimited: true }),
        expect: '不限',
      },
      {
        name: '只发参数（不画题名）',
        note: '「只发参数」那条路上题名由外面定，卡片里那一个输入框不画，其余照旧。',
        props: basic(PARAMS_ONLY),
        expect: '参与者类型',
      },
    ],
  },
  {
    id: 'task-form-time-card',
    title: 'TaskFormTimeCard',
    about: '时间设置那张卡：报名开始、报名截止、领取题目后的默认天数。',
    file: 'src/components/tasks/form/TaskFormTimeCard.vue',
    component: TaskFormTimeCard,
    needs: UI_T,
    states: [
      {
        name: '新发一道题',
        note: '报名开始空着（想起来再开），截止默认落在两周后 —— 这是调用方给的初值，卡片不管从哪来。',
        props: time(),
        expect: '时间设置',
      },
      {
        name: '改一道本来就不设截止的题',
        note: '改题时截止是空的：空和后端的 `null` 是同一件事，那一格底下那句「留空表示不设置报名截止时间」是提示（不该变成一条红字）。',
        props: time({ deadline: null }),
        expect: '报名截止日期',
      },
      {
        name: '天数填错了（标红）',
        note: '领取后多少天这条是必填的正整数，红字照样是 `defineField` 那一半。',
        props: time({ defaultDeadline: 0, defaultDeadlineControl: red('必须大于等于 1') }),
        expect: '必须大于等于 1',
      },
      {
        name: '今天以前的日子点不动',
        note: '两个日期框共用一条「不能选过去的日期」的规矩（`allowed-dates`），它是这一件自己的事：只跟这两个框有关，也不是数据。',
        props: time({ deadline: new Date(2020, 0, 1) }),
        expect: '时间设置',
      },
    ],
  },
  {
    id: 'task-form-classify-card',
    title: 'TaskFormClassifyCard',
    about: '分类标签那张卡：所属分类（一道题落在一个分类下）和主题标签（可以选好几个）。',
    file: 'src/components/tasks/form/TaskFormClassifyCard.vue',
    component: TaskFormClassifyCard,
    needs: UI_T,
    states: [
      {
        name: '选了分类和一个主题',
        note: '两张表都是算好了传进来的（`{ title, value }`），所以这一件既不认 `SpaceCategory` 也不认 `Topic`。',
        props: classify(),
        expect: '分类标签',
      },
      {
        name: '没有可选分类',
        note: '一个分类都没有的时候分类那一条整条不画（原来那句 `categories.length > 0`）：下拉里只有一句「没有项目可选」，比空着还费解。',
        props: classify({ categoryItems: [], categoryId: undefined }),
        expect: '话题',
      },
      {
        name: '分类还没选（标红）',
        note: '分类是必填的，没选就标红；主题一个都不选也算数。',
        props: classify({ categoryId: undefined, categoryIdControl: red('请选择所属分类') }),
        expect: '请选择所属分类',
      },
    ],
  },
  {
    id: 'task-form-real-name-card',
    title: 'TaskFormRealNameCard',
    about: '实名信息要求那张卡：一个开关，加上开关底下跟着变的那一块。',
    file: 'src/components/tasks/form/TaskFormRealNameCard.vue',
    component: TaskFormRealNameCard,
    needs: UI_T,
    states: [
      {
        name: '没开（默认）',
        note: '没开就说没开意味着什么（匿名可参与），不是留一块空白让人猜。',
        props: { requireRealName: false, requireRealNameControl: OK },
        expect: '未启用实名认证要求',
      },
      {
        name: '开了',
        note: '开了之后底下换成三条承诺；至于「提交前要不要先弹那段隐私说明」，那是提交那条路上的事（`useTaskForm.ts` 的第一道闸门），卡片不知道有弹窗。',
        props: { requireRealName: true, requireRealNameControl: OK },
        expect: '你已选择要求实名信息',
      },
    ],
  },
  {
    id: 'task-form-access-card',
    title: 'TaskFormAccessCard',
    about: '权限设置那张卡：一个开关，开了之后才出现的「哪些域名组能参与」多选。',
    file: 'src/components/tasks/form/TaskFormAccessCard.vue',
    component: TaskFormAccessCard,
    needs: UI_T,
    states: [
      {
        name: '没开',
        note: '没开的时候底下那一块整个不画：多选一个不该选的组，是这一步最容易犯的错。',
        props: {
          accessControlEnabled: false,
          accessDomainGroupIds: [],
          domainGroupItems: DOMAIN_GROUPS,
          accessControlEnabledControl: OK,
          accessDomainGroupIdsControl: OK,
        },
        expect: '权限设置',
      },
      {
        name: '开了，选了域名组',
        note: '每一组底下带一句它到底盖哪些域名（`subtitle`）——「校内网络」这四个字本身说不清谁能进。',
        props: {
          accessControlEnabled: true,
          accessDomainGroupIds: [1],
          domainGroupItems: DOMAIN_GROUPS,
          accessControlEnabledControl: OK,
          accessDomainGroupIdsControl: OK,
        },
        expect: '校内网络',
      },
      {
        name: '开了，但一个域名组都没有',
        note: '一个可选的组都没有时换成一条警告：开了开关却选不了任何东西，得说清楚是为什么。',
        props: {
          accessControlEnabled: true,
          accessDomainGroupIds: [],
          domainGroupItems: [],
          accessControlEnabledControl: OK,
          accessDomainGroupIdsControl: OK,
        },
        expect: '暂无可用域名组',
      },
    ],
  },
  {
    id: 'task-form-description-card',
    title: 'TaskFormDescriptionCard',
    about: '赛题详情那张卡：原来就是 Markdown 的题继续用纯文本域，其余走富文本。',
    file: 'src/components/tasks/form/TaskFormDescriptionCard.vue',
    component: TaskFormDescriptionCard,
    needs: UI_T,
    states: [
      {
        name: 'Markdown 题（纯文本域）',
        note: '原始格式是 Markdown 就还是 Markdown：换成富文本会把题面里那些记号洗掉，而它们本来是有用的。',
        props: {
          descriptionFormat: 'markdown',
          markdownDescription: '## 题目\n\n把 `flag` 找出来。',
          description: { type: 'doc', content: [{ type: 'paragraph' }] },
          parametersOnly: false,
        },
        expect: '题目详情（Markdown 格式）',
      },
      {
        name: '富文本题',
        note: '其余的题走富文本编辑器，存 JSON；工具栏与实况文档是同一排，另加代码块、插图和表格。',
        props: {
          descriptionFormat: 'tiptap',
          markdownDescription: '',
          description: {
            type: 'doc',
            content: [
              { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '题目' }] },
              { type: 'paragraph', content: [{ type: 'text', text: '把 flag 找出来。' }] },
            ],
          },
          parametersOnly: false,
        },
        expect: '把 flag 找出来。',
      },
      {
        name: '只发参数（整张卡不画）',
        note:
          '「只发参数」那条路上没有题面这回事，两个编辑器都不出现（交出去时正文是空串）。' +
          '这一格画出来的是一个占位注释，机械验收在这里只确认它挂得起来 —— 「一个字都不画」' +
          '不是靠这一格证明的，是靠上面两格确实画出了编辑器。',
        props: {
          descriptionFormat: 'tiptap',
          markdownDescription: '',
          description: { type: 'doc', content: [{ type: 'paragraph' }] },
          parametersOnly: true,
        },
      },
    ],
  },
  {
    id: 'task-form-video-card',
    title: 'TaskFormVideoCard',
    about: '视频链接那张卡：一个选填的地址和一句「支持 Bilibili」的提示。',
    file: 'src/components/tasks/form/TaskFormVideoCard.vue',
    component: TaskFormVideoCard,
    needs: UI_T,
    states: [
      {
        name: '空着',
        note: '选填：空着就是没有视频，提示一直摆在那儿（`persistent-hint`），不然填完才发现填错了站。',
        props: { videoUrl: '', videoUrlControl: OK, parametersOnly: false },
        expect: '支持 Bilibili 视频链接',
      },
      {
        name: '填了一个链接',
        note: '值进值出。这个地址最后能不能解析、不支持的站要不要拦一道，是提交那条路上的第二道闸门，卡片不知道。',
        props: { videoUrl: 'https://www.bilibili.com/video/BV1xx411c7mD', videoUrlControl: OK, parametersOnly: false },
        expect: '支持 Bilibili 视频链接',
      },
      {
        name: '不是 https（标红）',
        note: 'schema 里只拦「不是 https」这一种；http 的 B 站链接也拦（嵌不了，还会被浏览器降级）。',
        props: {
          videoUrl: 'http://example.com/v/1',
          videoUrlControl: red('请输入有效的 HTTPS 链接'),
          parametersOnly: false,
        },
        expect: '请输入有效的 HTTPS 链接',
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
        note: '它只画这一段说明，人点的是哪一颗往外报一声；要不要弹、弹完之后那份提交怎么办全在 `useTaskForm.ts` 里。`persistent`：点外面点不掉 —— 这是一份要读完的说明。',
        props: { open: true },
        expect: '实名信息隐私保护',
      },
    ],
  },
  {
    id: 'task-form-video-dialog',
    title: 'TaskFormVideoDialog',
    about: '「视频链接提示」那段：填了一个解析不了的地址时问一句「还存吗」。',
    file: 'src/components/tasks/form/TaskFormVideoDialog.vue',
    component: TaskFormVideoDialog,
    needs: UI_T,
    teleport: true,
    states: [
      {
        name: '弹着',
        note: '现在只有 B 站能嵌着放，别的站存下来是一条点开能看、题面里放不出来的链接 —— 所以问一句，而不是拦死。',
        props: { open: true },
        expect: '视频链接提示',
      },
    ],
  },
  {
    id: 'task-form',
    title: 'TaskForm',
    about: '整张发题 / 改题的表：七张卡接在 `useTaskForm.ts` 的十六对字段上，底下是提交和取消。',
    file: 'src/components/tasks/TaskForm.vue',
    component: TaskForm,
    needs: UI_T,
    states: [
      {
        name: '新发一道题',
        note: '这一层自己不画字段：值走 `v-model:<字段>`、`defineField` 的另一半走 `:control`，规矩都在 composable 里。题面用 Markdown 那一路，富文本那一格在 TaskFormDescriptionCard 里。',
        props: { submitButtonText: '提交', classificationTopics: [], descriptionFormat: 'markdown' },
        expect: '基本信息',
      },
      {
        name: '只发参数那条路',
        note: '只发参数时题名和题面两张卡整个不画，其余照旧。',
        props: {
          submitButtonText: '提交',
          classificationTopics: [],
          descriptionFormat: 'markdown',
          parametersOnly: true,
        },
        expect: '参与者类型',
      },
    ],
  },
]
