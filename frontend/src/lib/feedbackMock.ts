/**
 * feedbackMock.ts — 反馈功能的**纯前端原型**数据层。
 *
 * 这个文件里没有一个网络请求，也**不应该**有：本轮要的是「页面结构、路由和交互
 * 做完整」，schema / 存储 / Agent SendFeedback / 去重 / 管理员后端流程是下一轮单独
 * 研究的事（见任务 ccec43d0）。所以类型、状态表、种子数据全在这里，唯一的消费者是
 * stores/feedback.ts，将来接真接口时被替换掉的也正是这两层。
 *
 * 三条自我约束，目的是让原型不会有「上了真数据才发现」的偏差：
 *
 *   1. 状态只有一套梯子（STATUS_LADDER）。需求里状态列了 5 个（已收录 / 评估中 /
 *      计划中 / 处理中 / 已解决），右侧 Timeline 写的是 4 步（已收录 → 评估中 →
 *      计划中 → 已上线）。两处对不上会让「这条在时间线上走到哪」在实现时变成两次
 *      推断，所以这里统一成 5 步的**同一套**，Timeline 显示的就是它。已解决与已上线
 *      是否要拆成两个状态，留给下一轮的 schema 讨论——**这里没有替那个决定下结论**。
 *   2. 状态的颜色只有一处定义（STATUS_META），卡片、详情、管理员列表都读它。设计
 *      系统里状态色是三件套（mark / ink / wash），散着写必然会出现「卡片和详情说的
 *      不是同一个颜色」。
 *   3. 时间一律是「相对现在」算出来的 ISO 串。写死某个日期的话，原型放两天再打开，
 *      列表里全是「3天前」，看着就像坏了。
 */

export type FeedbackKind = 'bug' | 'suggestion' | 'other'
export type FeedbackStatus = 'received' | 'triaging' | 'planned' | 'in_progress' | 'resolved'
export type FeedbackVisibility = 'public' | 'private'
/** 来源：用户提交 / Agent 发现。管理员那一栏按它分 Tab。 */
export type FeedbackSource = 'user' | 'agent'
export type FeedbackPriority = 'low' | 'normal' | 'high' | 'urgent'
export type FeedbackRole = 'user' | 'admin'

export interface FeedbackComment {
  id: string
  author: string
  /** AI 队友的评论：头像和名字的呈现与人不完全一样，所以它得是个字段。 */
  byAgent?: boolean
  body: string
  createdAt: string
  /**
   * 这条回复的是哪一条评论（顶层评论的 id）。没有它就是顶层。
   *
   * **只有两层**：回复的回复也指向那个顶层父级。这是原型里刻意留着的一条限制，
   * 因为「要不要无限嵌套」是这一轮要评审的问题之一 —— 三种评论布局里只有一种
   * 用得上它，另外两种按时间平铺，`parentId` 到了界面上就被丢掉了。
   */
  parentId?: string
}

export interface FeedbackTimelineEntry {
  status: FeedbackStatus
  at: string
  /** 谁推动的。管理员推进时是 handle，Agent 发现时是「芝士」。 */
  by?: string
}

export interface FeedbackItem {
  id: string
  title: string
  /** 列表里那一句。详情页顶部不重复显示。 */
  summary: string
  kind: FeedbackKind
  status: FeedbackStatus
  visibility: FeedbackVisibility
  source: FeedbackSource
  priority: FeedbackPriority
  author: string
  createdAt: string
  supports: number
  supportedByMe: boolean
  views: number
  tags: string[]
  comments: FeedbackComment[]
  /** 详情页正文三段。用户提交的都有，Agent 发现的用 whatHappened/repro/evidence。 */
  problem: string
  why: string
  expectation: string
  timeline: FeedbackTimelineEntry[]
  /* ---- 下面这些只有部分条目有：Agent 发现的带现场，管理员侧才有内部备注 ---- */
  whatHappened?: string
  repro?: string
  evidence?: string
  logs?: string
  sessionId?: string
  environment?: string
  /** 仅管理员可见的内部备注；**详情页永远不显示它**。 */
  internalNote?: string
  assignee?: string | null
  /** 安全问题单独一栏，且永远不公开。 */
  security?: boolean
}

/* -------------------------------------------------------------------------
   状态表：标签、颜色、顺序。颜色只写 token 名，不写 hex —— 写死的颜色在两个
   主题里必然错一个（docs/design-system.md），而 stylelint 会把 hex 判成新违规。
   `wash` 是底色，`ink` 是同一底色上的可读文字色，两者成对出现、不可混用。
   ------------------------------------------------------------------------- */

export interface StatusMeta {
  label: string
  wash: string
  ink: string
  dot: string
}

export const STATUS_META: Record<FeedbackStatus, StatusMeta> = {
  received: { label: '已收录', wash: 'var(--fill)', ink: 'var(--muted)', dot: 'var(--faint)' },
  triaging: { label: '评估中', wash: 'var(--warn-wash)', ink: 'var(--warn-ink)', dot: 'var(--warn)' },
  planned: { label: '计划中', wash: 'var(--fill-2)', ink: 'var(--text)', dot: 'var(--muted)' },
  in_progress: { label: '处理中', wash: 'var(--warn-wash)', ink: 'var(--warn-ink)', dot: 'var(--warn)' },
  resolved: { label: '已解决', wash: 'var(--ok-wash)', ink: 'var(--ok-ink)', dot: 'var(--ok)' },
}

/** 梯子的**唯一**一份顺序。Timeline 和「处理中 / 已解决」两个 Tab 都从这里取，
 *  不各写一遍数组——那样加一个状态就要改三处，而漏掉的那处不会报错。 */
export const STATUS_LADDER: FeedbackStatus[] = ['received', 'triaging', 'planned', 'in_progress', 'resolved']

export const KIND_LABEL: Record<FeedbackKind, string> = {
  bug: 'Bug',
  suggestion: '建议',
  other: '其他',
}

export const PRIORITY_META: Record<FeedbackPriority, StatusMeta> = {
  low: { label: '低', wash: 'var(--fill)', ink: 'var(--muted)', dot: 'var(--faint)' },
  normal: { label: '普通', wash: 'var(--fill-2)', ink: 'var(--text)', dot: 'var(--muted)' },
  high: { label: '高', wash: 'var(--warn-wash)', ink: 'var(--warn-ink)', dot: 'var(--warn)' },
  urgent: { label: '紧急', wash: 'var(--danger-wash)', ink: 'var(--danger-ink)', dot: 'var(--danger)' },
}

export const SOURCE_LABEL: Record<FeedbackSource, string> = {
  user: '用户提交',
  agent: 'Agent 发现',
}

export const ASSIGNEES = ['andylizf', 'n1ctheboy', 'ligan', 'maxiaoyu', 'chiruotong']

/* -------------------------------------------------------------------------
   种子数据。写的是真的会发生在这个平台上的反馈 —— 附件显示不出来、PDF 打不开
   这类已经在项目记忆里落过案的东西，比「按钮点了没反应」更能看出列表字段够不够用。
   ------------------------------------------------------------------------- */

const HOUR = 3600 * 1000
const ago = (hours: number) => new Date(Date.now() - hours * HOUR).toISOString()
const daysAgo = (days: number) => ago(days * 24)

const comment = (
  id: string,
  author: string,
  body: string,
  hours: number,
  extra: { byAgent?: boolean; parentId?: string } = {}
): FeedbackComment => ({
  id,
  author,
  body,
  createdAt: ago(hours),
  ...extra,
})

export function seedFeedback(): FeedbackItem[] {
  return [
    {
      id: 'FB-1042',
      title: '聊天区里上传的图片显示不出来',
      summary: '消息里的图片位置是一个破图图标，右键在新标签页打开返回 401 JSON。',
      kind: 'bug',
      status: 'in_progress',
      visibility: 'public',
      source: 'user',
      priority: 'urgent',
      author: 'wangchangxin',
      createdAt: daysAgo(6),
      supports: 27,
      supportedByMe: false,
      views: 412,
      tags: ['附件', '鉴权'],
      comments: [
        comment('c1', 'n1ctheboy', '复现了，同一张图放进文档面板就能看。', 120),
        comment('c2', '芝士', '已定位到鉴权方式：`<img>` 发不出自定义头，详情见下。', 118, { byAgent: true }),
        // 这两条是**回复**（parentId 指向 c2），专门留给「楼中楼」那一版布局看的：
        // 回复的回复也挂在 c2 底下，不往第三层缩进。
        comment('c4', 'andylizf', '那详情页里的附件预览是怎么拿到图的？走的是另一个域名吗。', 116, { parentId: 'c2' }),
        comment('c5', '芝士', '同一个接口，但它读的是 blob 而不是 `<img src>`。', 115, { parentId: 'c2' }),
        comment('c3', 'andylizf', '已排进本迭代。', 30),
      ],
      problem:
        '在话题聊天里上传图片后，消息气泡里只显示一个破图图标。右键「在新标签页打开」会返回一段 JSON，写着 Login required。',
      why: '图片是排查问题时最快的一种说明方式。上传成功却看不见，等于这条消息没有任何信息量，还得让提问的人改用文字再描述一遍。',
      expectation: '上传完成的图片直接显示在消息里；另外希望能点开看大图，而不是跳到一个新标签页。',
      timeline: [
        { status: 'received', at: daysAgo(6), by: '芝士' },
        { status: 'triaging', at: daysAgo(5), by: 'andylizf' },
        { status: 'planned', at: daysAgo(3), by: 'andylizf' },
        { status: 'in_progress', at: daysAgo(1), by: 'n1ctheboy' },
      ],
    },
    {
      id: 'FB-1039',
      title: 'PDF 预览一直显示「无法显示这个文档」',
      summary: '同一份 PDF 在别的浏览器能打开，在这台机器上不行。',
      kind: 'bug',
      status: 'resolved',
      visibility: 'public',
      source: 'user',
      priority: 'high',
      author: 'maxiaoyu',
      createdAt: daysAgo(11),
      supports: 19,
      supportedByMe: true,
      views: 305,
      tags: ['预览', '兼容性'],
      comments: [
        comment('c1', '芝士', '根因是浏览器太老：pdf.js 用了一个 2025 年 9 月才有的新方法。', 200, { byAgent: true }),
        comment('c2', 'maxiaoyu', '换成最新版 Chrome 就正常了，确认。', 190),
      ],
      problem: '点开面板里的 PDF 只有一屏「无法显示这个文档」，没有任何细节可看。',
      why: '交付物大多是 PDF。打不开就只能下载下来再看，手机上这一条尤其难受。',
      expectation: '能直接在面板里看；实在打不开，也希望能告诉我为什么。',
      timeline: [
        { status: 'received', at: daysAgo(11), by: '芝士' },
        { status: 'triaging', at: daysAgo(10), by: 'andylizf' },
        { status: 'planned', at: daysAgo(8), by: 'andylizf' },
        { status: 'in_progress', at: daysAgo(5), by: 'n1ctheboy' },
        { status: 'resolved', at: daysAgo(2), by: 'n1ctheboy' },
      ],
    },
    {
      id: 'FB-1051',
      title: '希望反馈可以直接从对话里发起',
      summary: '排查完之后还要切页面、重打一遍描述，中间那段上下文全丢了。',
      kind: 'suggestion',
      status: 'triaging',
      visibility: 'public',
      source: 'user',
      priority: 'normal',
      author: 'ligan',
      createdAt: daysAgo(3),
      supports: 14,
      supportedByMe: false,
      views: 156,
      tags: ['体验'],
      comments: [comment('c1', '芝士', '同意——上下文正是最值钱的那部分，重打一遍就没了。', 20, { byAgent: true })],
      problem: '发现是平台的问题之后，要另外打开反馈中心，把刚才聊过的内容重新描述一遍。',
      why: '问题是在对话里发现的，现场也在对话里。换一个页面重新打字，等于让人把最有价值的上下文（复现步骤、会话链接）丢掉。',
      expectation: '对话里能直接提交，自动带上问题摘要和会话链接。',
      timeline: [
        { status: 'received', at: daysAgo(3), by: '芝士' },
        { status: 'triaging', at: daysAgo(1), by: 'andylizf' },
      ],
    },
    {
      id: 'FB-1044',
      title: '手机上底部导航挡住输入框',
      summary: '软键盘弹出后，输入框被压到底栏下面，看不见自己在打什么。',
      kind: 'bug',
      status: 'planned',
      visibility: 'public',
      source: 'user',
      priority: 'high',
      author: 'chiruotong',
      createdAt: daysAgo(9),
      supports: 22,
      supportedByMe: false,
      views: 268,
      tags: ['移动端', '布局'],
      comments: [comment('c1', 'pengwenbo', 'iOS Safari 上必现，Chrome 手机上偶尔。', 150)],
      problem: '手机浏览器里点输入框，键盘顶上来之后输入框跑到屏幕外了。',
      why: '手机上回消息是最高频的动作，一次都打不了字等于这个平台在手机上不可用。',
      expectation: '键盘弹出时输入框跟着抬起来。',
      timeline: [
        { status: 'received', at: daysAgo(9), by: '芝士' },
        { status: 'triaging', at: daysAgo(7), by: 'andylizf' },
        { status: 'planned', at: daysAgo(4), by: 'andylizf' },
      ],
    },
    {
      id: 'FB-1033',
      title: '项目列表刷新失败时应该保留上一次的内容',
      summary: '网络抖一下，整个项目列表变成空的，看着像项目都没了。',
      kind: 'suggestion',
      status: 'resolved',
      visibility: 'public',
      source: 'user',
      priority: 'normal',
      author: 'caisongyang',
      createdAt: daysAgo(16),
      supports: 9,
      supportedByMe: false,
      views: 121,
      tags: ['稳定性'],
      comments: [],
      problem: '切回首页时偶尔看到「暂无项目」，过几秒又回来了。',
      why: '空列表是一个很强的信号，读的人第一反应是东西丢了，不是网络抖了一下。',
      expectation: '刷新失败就继续显示上次成功加载的内容，并提示一下。',
      timeline: [
        { status: 'received', at: daysAgo(16), by: '芝士' },
        { status: 'planned', at: daysAgo(12), by: 'andylizf' },
        { status: 'resolved', at: daysAgo(6), by: 'andylizf' },
      ],
    },
    {
      id: 'FB-1058',
      title: '搜索框应该记住上一次的筛选条件',
      summary: '每次进搜索页都要重新选一遍类型和范围。',
      kind: 'suggestion',
      status: 'received',
      visibility: 'public',
      source: 'user',
      priority: 'low',
      author: 'pengwenbo',
      createdAt: daysAgo(1),
      supports: 5,
      supportedByMe: false,
      views: 41,
      tags: ['搜索'],
      comments: [],
      problem: '搜索页的筛选条件每次进来都是默认值。',
      why: '连着找同一类东西的时候，每次都要重新点一遍。',
      expectation: '记住上一次的选择。',
      timeline: [{ status: 'received', at: daysAgo(1), by: '芝士' }],
    },
    {
      id: 'FB-1027',
      title: '浅色主题下代码块的对比度偏低',
      summary: '浅色主题里注释几乎看不清，长时间读很累。',
      kind: 'bug',
      status: 'resolved',
      visibility: 'public',
      source: 'user',
      priority: 'normal',
      author: 'andylizf',
      createdAt: daysAgo(21),
      supports: 11,
      supportedByMe: false,
      views: 176,
      tags: ['设计系统', '无障碍'],
      comments: [comment('c1', 'wangchangxin', '对照度测过之后重挑了一版色值。', 300)],
      problem: '浅色主题里代码块的注释颜色太淡。',
      why: '读代码是这里的日常，配色不能只是好看。',
      expectation: '达到 AA 对比度。',
      timeline: [
        { status: 'received', at: daysAgo(21), by: '芝士' },
        { status: 'planned', at: daysAgo(18), by: 'wangchangxin' },
        { status: 'resolved', at: daysAgo(14), by: 'wangchangxin' },
      ],
    },
    /* ---- 别人提的私密反馈：我看不见，只有他本人和管理员看得见 ---- */
    {
      id: 'FB-1055',
      title: '我的会话记录里出现了别人的文件名',
      summary: '在项目文档的最近列表里看到一个不属于我的路径。',
      kind: 'bug',
      status: 'triaging',
      visibility: 'private',
      source: 'user',
      priority: 'urgent',
      author: 'chiruotong',
      createdAt: ago(9),
      supports: 0,
      supportedByMe: false,
      views: 3,
      tags: ['隐私'],
      comments: [],
      problem: '项目文档面板的最近列表里有一条路径不是我建的，也从来没打开过。',
      why: '不确定是不是缓存串了，如果是别人看不到就算了，如果反过来我不希望发生。',
      expectation: '确认一下这个列表的数据来源。',
      timeline: [
        { status: 'received', at: ago(9), by: '芝士' },
        { status: 'triaging', at: ago(6), by: 'andylizf' },
      ],
      sessionId: 'sess_7c21a4',
      environment: 'Chrome 141 / macOS 15.3 / PWA 安装版',
      logs: '[12:04:11] docs.recent.load project=p_8812 items=6\n[12:04:11]   item[4] path=users/maxiaoyu/notes.md',
      internalNote: '先按「缓存串了」查，确认不是越权再回。暂时不回复用户。',
      assignee: 'n1ctheboy',
    },
    /* ---- 我自己提的私密反馈：对**别人**不存在，对我照常显示 ---- */
    {
      id: 'FB-1061',
      title: '希望个人主页能隐藏我加入的项目',
      summary: '第三方拿到这个链接，就能看到我参与过哪些项目。',
      kind: 'suggestion',
      status: 'triaging',
      visibility: 'private',
      source: 'user',
      priority: 'normal',
      // 作者写死成「我」是**故意的**：它就是「当前用户自己提的那一条」，好让预览里
      // 一打开就看得见「私密反馈对提交者本人可见」长什么样（中心页列表里带锁的那张
      // 卡）。真接后端时这里由 author_id 决定，跟名字无关。
      author: '我',
      createdAt: ago(28),
      supports: 0,
      supportedByMe: false,
      views: 4,
      tags: ['隐私', '个人主页'],
      comments: [],
      problem: '个人主页对任何拿到链接的人可见，上面列着我加入过的全部项目。有几个项目我不想让人一眼看到。',
      why: '分享主页本来只是想让人看到我写的东西，不是把我在哪些项目里出现过一起交出去。',
      expectation: '能在设置里挑几个项目不显示；默认保持现在的样子。',
      // 私密反馈**没有** timeline 以外的东西：它不进公开列表，也不参与「热门」排序。
      timeline: [
        { status: 'received', at: ago(28), by: '我' },
        { status: 'triaging', at: ago(20), by: 'andylizf' },
      ],
    },
    {
      id: 'FB-1053',
      title: '导出的表格里公式结果是空的',
      summary: '导出的 xlsx 公式栏是对的，显示出来的数是旧的。',
      kind: 'bug',
      status: 'planned',
      visibility: 'public',
      source: 'user',
      priority: 'high',
      author: 'ligan',
      createdAt: daysAgo(4),
      supports: 8,
      supportedByMe: false,
      views: 97,
      tags: ['导出'],
      comments: [],
      problem: '从数据面板导出的表格，公式还在但结果没算。',
      why: '导出的表是拿去汇报的，数字不对比没有更糟。',
      expectation: '导出时把公式算成值。',
      timeline: [
        { status: 'received', at: daysAgo(4), by: '芝士' },
        { status: 'triaging', at: daysAgo(3), by: 'andylizf' },
        { status: 'planned', at: daysAgo(2), by: 'andylizf' },
      ],
    },
    /* ---- Agent 发现的：整条反馈由芝士自己整理好，人只要决定发不发 ---- */
    {
      id: 'FB-1057',
      title: '连续两次上传同名附件，第二次会静默失败',
      summary: 'Agent 在帮助用户排查上传问题时发现：同名文件被去重逻辑吞掉，界面不说。',
      kind: 'bug',
      status: 'received',
      visibility: 'public',
      source: 'agent',
      priority: 'normal',
      author: '芝士',
      createdAt: ago(5),
      supports: 3,
      supportedByMe: false,
      views: 28,
      tags: ['附件'],
      comments: [],
      problem: '在同一个话题里两次上传同名文件时，第二条附件不会出现在消息里，也没有任何提示。',
      why: '看不出失败的上传，会被当成「已经传过了」——排查问题的人于是拿着一份旧文件在分析。',
      expectation: '同名时自动改名，或者明确告诉用户这一次没有被加上。',
      timeline: [{ status: 'received', at: ago(5), by: '芝士' }],
      whatHappened: '用户上传 `error.log` 两次，第一次成功，第二次没有任何反应。',
      repro: '1. 新建话题\n2. 上传 a.log\n3. 再上传一份同名但内容不同的 a.log\n4. 消息里仍然只有第一条附件',
      evidence: '接口返回 200，但 attachment 列表长度没变；后端没有同名冲突的提示。',
      sessionId: 'sess_2f90bd',
      environment: 'Chrome 141 / Linux',
    },
    {
      id: 'FB-1056',
      title: '任务清单里的英文动词没有翻译',
      summary: 'Agent 发现：施工现场里 90% 的工具参数是英文原文。',
      kind: 'suggestion',
      status: 'triaging',
      visibility: 'public',
      source: 'agent',
      priority: 'normal',
      author: '芝士',
      createdAt: daysAgo(2),
      supports: 6,
      supportedByMe: false,
      views: 63,
      tags: ['中文化'],
      comments: [comment('c1', 'wangchangxin', '先把 Bash 的 description 取出来用吧，那个是现成的。', 40)],
      problem: '施工现场那一栏里，工具调用显示的是参数原文，绝大多数是英文。',
      why: '读的人要在脑子里翻译一遍才知道发生了什么。',
      expectation: '显示工具自带的描述字段，而不是原始参数。',
      timeline: [
        { status: 'received', at: daysAgo(2), by: '芝士' },
        { status: 'triaging', at: daysAgo(1), by: 'andylizf' },
      ],
      whatHappened: '回放 200 个会话记录，施工现场里 89.7% 的参数是纯英文。',
      repro: '打开任意一个跑过很多次命令的话题，看右侧「现场」。',
      evidence: '18126 次工具调用里 16196 次的首个参数不含中文。',
      sessionId: 'sess_replay_200',
    },
    /* ---- 安全问题：永远不公开，单独一栏 ---- */
    {
      id: 'FB-1059',
      title: '邀请码接口没有作用域，也没有管理员门禁',
      summary: 'Agent 审计发现：三个管理端点只校验登录，且邀请码是不可复用的注册门。',
      kind: 'bug',
      status: 'in_progress',
      visibility: 'private',
      source: 'agent',
      priority: 'urgent',
      author: '芝士',
      createdAt: daysAgo(1),
      supports: 0,
      supportedByMe: false,
      views: 2,
      tags: ['安全'],
      comments: [],
      problem: '邀请码没有 space_id、不记录使用人、也没有前端界面；三个管理端点只用了 require_auth_user。',
      why: '任何登录用户都能改注册门。',
      expectation: '补上管理员门禁，并明确邀请码的作用域。',
      timeline: [
        { status: 'received', at: daysAgo(1), by: '芝士' },
        { status: 'triaging', at: ago(20), by: 'n1ctheboy' },
        { status: 'in_progress', at: ago(6), by: 'n1ctheboy' },
      ],
      whatHappened: '按仓库约定逐一核对了写路由的鉴权，发现三个管理端点没有管理员门禁。',
      repro: '用任意普通账号调用这三个端点，均返回 200。',
      evidence: 'backend/app/api/routes/spaces.py：三个端点只依赖 require_auth_user。',
      sessionId: 'sess_audit_0918',
      environment: 'main @ 7b9e4ce4f',
      security: true,
      internalNote: '**不要**在任何用户可见的地方提这条。修复前先别动线上数据。',
      assignee: 'n1ctheboy',
    },
    {
      id: 'FB-1049',
      title: '缺少频率限制，登录接口可被暴力尝试',
      summary: 'Agent 审计发现：登录接口没有失败次数限制。',
      kind: 'bug',
      status: 'planned',
      visibility: 'private',
      source: 'agent',
      priority: 'high',
      author: '芝士',
      createdAt: daysAgo(7),
      supports: 0,
      supportedByMe: false,
      views: 5,
      tags: ['安全'],
      comments: [],
      problem: '登录接口对失败次数没有任何限制。',
      why: '弱口令可以被离线式地慢慢试。',
      expectation: '加上按账号与来源的限制。',
      timeline: [
        { status: 'received', at: daysAgo(7), by: '芝士' },
        { status: 'triaging', at: daysAgo(6), by: 'andylizf' },
        { status: 'planned', at: daysAgo(4), by: 'andylizf' },
      ],
      whatHappened: '审计登录链路时发现没有任何限流或锁定。',
      repro: '连续提交错误口令 100 次，全部正常返回，没有被拒绝或延迟。',
      evidence: 'backend/app/api/routes/users.py：登录端点没有任何速率控制。',
      sessionId: 'sess_audit_0908',
      security: true,
      internalNote: '和 SSO 的改造一起做，别单独发一个 hotfix 打草惊蛇。',
      assignee: 'andylizf',
    },
  ]
}
