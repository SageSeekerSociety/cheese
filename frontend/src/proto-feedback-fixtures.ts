/**
 * proto-feedback-fixtures.ts — 预览构建的假数据，以及一个只认反馈接口的 fetch 拦截器。
 *
 * **这一份只被 `src/proto-feedback.ts` 引用**（预览的精简入口），应用本体一行都不碰：
 * 页面照旧通过 store → `api.ts` → `fetch` 取数，这里在 `fetch` 那一层把反馈的几条路由
 * 接过去。上一轮的做法是让 store 直接读一个内存 mock（`lib/feedbackMock.ts`），好处是
 * 简单，代价是**应用代码里长着一个只有预览才走的岔路**；这一轮页面接真接口，岔路挪到了
 * 构建产物里 —— 真实构建（`vite.config.ts`）不含这个文件，预览构建（
 * `vite.feedback-proto.config.mts`）才 import 它。
 *
 * 为什么预览需要它：预览通道给的是一个**单文件 HTML 和一个静态服务**，没有后端、也没有
 * 登录态（`/api/feedback/*` 全要鉴权），所以真接口在那边一律不通。不接这一层，人点开
 * 看到的就是一个空列表 —— 而「预览是白的」正是当初做这个入口要解决的事。
 *
 * 因此：**界面是真的，数据是假的**。要看真实数据请在本机起 backend + frontend。
 * 假数据照着 `backend/app/domain/feedback/models.py` 的字段造，覆盖几种要看的东西：
 * 私密条目（没有支持按钮、只挂中性标签）、agent 发现人代提交的那条（两个 handle 不同）、
 * 已修复的 bug（默认沉底）、走完四级停在「已上线」的（沉底规则必须和「已修复」一致）、
 * 支持数过门槛的（进「热门」）、带安全标记的（管理端第四栏）。
 */
import type {
  FeedbackCard,
  FeedbackComment,
  FeedbackCounts,
  FeedbackCreateBody,
  FeedbackDetail,
  FeedbackKind,
  FeedbackMeta,
  FeedbackNote,
  FeedbackPriority,
  FeedbackStatus,
  FeedbackVisibility,
} from '@/cx_types'

/** 预览里「我」是谁。管理端入口和「我的」那一栏都看它。 */
const ME = 'andy'
/** 「热门」的三个数与后端那三个常量（`repositories.HOT_SCORE` 等）取同一个值 ——
 *  预览要能照真话把这一栏的规则说出来（「两周前的一票算今天半票 · 至少 5 条」）。 */
const HOT_SCORE = 2.0
const HOT_HALF_LIFE_DAYS = 14
const HOT_MIN_ITEMS = 5
/** 预览一律按管理员看：私密条目、安全标记、管理端那一屏都要能点到。 */
const IS_ADMIN = true

/**
 * 「谁挑过头像」——接口的 `author_avatar_id` 在真实现里来自 `UserProfile.avatar_id`，
 * 而且**只装真的挑过的人**：注册时会给人人都写上那张全局默认头像，所以「有 avatar_id」
 * 不等于「挑过」，后端专门有一处把这两种分开（`chosen_avatar_ids`）。
 *
 * 这份假数据照同一件事造：名单里没有的返回 null，界面就画那个按 handle 派生的彩色
 * 首字母。**不要**图省事一律给个 id —— 那正是「所有没挑过头像的人共用同一张脸」，
 * 而区分人正是头像唯一的活。
 */
const CHOSEN_AVATARS: Record<string, number> = {
  andy: 3,
  andylizf: 2,
  chiruotong: 4,
}

function avatarOf(handle: string): number | null {
  return CHOSEN_AVATARS[handle] ?? null
}

/** 相对时间按**打开页面的那一刻**算：预览是拿来看「长什么样」的，把一个日期写死，
 *  过几天再打开就全是「8 天前」，而那不是任何人的真实体验。 */
const BASE_MS = Date.now()
function ago(minutes: number): string {
  return new Date(BASE_MS - minutes * 60_000).toISOString()
}

const META: FeedbackMeta = {
  kinds: ['bug', 'suggestion', 'other'],
  statuses: ['received', 'in_progress', 'resolved', 'deployed'],
  priorities: ['low', 'normal', 'high', 'urgent'],
  visibilities: ['public', 'private'],
  status_ladder: ['received', 'in_progress', 'resolved', 'deployed'],
  tabs: ['all', 'hot', 'active', 'resolved'],
  admin_tabs: ['public', 'private', 'agent', 'security'],
  hot_score: HOT_SCORE,
  hot_half_life_days: HOT_HALF_LIFE_DAYS,
  hot_min_items: HOT_MIN_ITEMS,
  is_admin: IS_ADMIN,
}

/** 造一条反馈。卡片字段和详情字段放在一份数据里 —— 详情页要的就是卡片 + 正文，
 *  分成两张表只会让「详情里的支持数」和「列表里的支持数」有对不上的机会。 */
function row(spec: {
  id: string
  display: string
  kind: FeedbackKind
  title: string
  summary: string
  status: FeedbackStatus
  priority?: FeedbackPriority
  visibility?: FeedbackVisibility
  security?: boolean
  author: string
  authorIsAgent?: boolean
  submittedBy?: string | null
  assignee?: string | null
  tags?: string[]
  supports: number
  supported?: boolean
  minutesAgo: number
  problem: string
  why?: string
  expectation?: string
  whatHappened?: string
  repro?: string
  evidence?: string
  environment?: string
  thread?: FeedbackComment[]
  notes?: FeedbackNote[]
  /** 默认 true —— 理由写在下面那一行旁边。 */
  can_delete?: boolean
}): FeedbackDetail {
  const created = ago(spec.minutesAgo)
  const thread = threadOf(spec.thread ?? [])
  return {
    id: spec.id,
    display_id: spec.display,
    kind: spec.kind,
    title: spec.title,
    summary: spec.summary,
    status: spec.status,
    priority: spec.priority ?? 'normal',
    visibility: spec.visibility ?? 'public',
    security: spec.security ?? false,
    author_handle: spec.author,
    author_is_agent: spec.authorIsAgent ?? false,
    author_avatar_id: avatarOf(spec.author),
    submitted_by_handle: spec.submittedBy ?? null,
    assignee_handle: spec.assignee ?? null,
    tags: spec.tags ?? [],
    supports: spec.supports,
    comments: thread.length,
    supported: spec.supported ?? false,
    last_activity_at: created,
    created_at: created,
    problem: spec.problem,
    why: spec.why ?? null,
    expectation: spec.expectation ?? null,
    what_happened: spec.whatHappened ?? null,
    repro: spec.repro ?? null,
    evidence: spec.evidence ?? null,
    logs: null,
    session_id: null,
    environment: spec.environment ?? null,
    topic_id: null,
    project_id: null,
    timeline: ladderUpTo(spec.status, created),
    thread,
    // 详情默认给第一页；下一页的游标由 `detailPage` 按当时那一条算 —— 写死在这里
    // 就等于「谁都一页装得下」，那个按钮在预览里根本不会出现。
    thread_next_cursor: null,
    notes: spec.notes ?? [],
    // 预览里的人**就是这几条的提交者**（假后端没有登录态可言），按服务端的判据他
    // 正是能删的那一位。这一格不能写死 `false`：那会让「删除」这个入口在预览里一次
    // 都不出现，而预览正是拿来看这类东西的地方。
    can_delete: spec.can_delete ?? true,
  }
}

/** 预览里每一页**顶层评论**给几条。真实现是 `THREAD_PAGE = 50`，一屏放不下 50 条，
 *  于是「加载更多评论」这个按钮在预览里永远不出现 —— 而它正是这一版新加的东西之一。
 *  压到 2 条是为了让人点得到它。**分页的边界是这份假数据自己的事**，store 那边一行
 *  都不知道（它只认服务端发下来的那个游标）。
 *
 *  一栋楼里的回复**一次给全**，所以 `replies_next_cursor` 恒为 null、楼内那个「加载
 *  更多回复（还有 N 条）」在预览里点不出来；「展开更多 N 条回复」那一个是本地折的，
 *  和它无关，照常能点。 */
const PROTO_THREAD_PAGE = 2

/** 顶层评论带上「这一栋一共几条回复」。服务端那个数是 `page_comments` 一次查出来
 *  的（不是这一页里有几条），客户端靠它决定「展开更多」是摊开手上这几条、还是去取
 *  下一页。假数据这边整条线程都在手上，数一遍就行。 */
function threadOf(thread: FeedbackComment[]): FeedbackComment[] {
  const replies = new Map<string, number>()
  for (const c of thread) {
    if (c.parent_id) replies.set(c.parent_id, (replies.get(c.parent_id) ?? 0) + 1)
  }
  for (const c of thread) {
    if (c.parent_id === null) c.reply_count = replies.get(c.id) ?? 0
  }
  return thread
}

/** 一页顶层评论 + **它们各自那一栋的全部回复**（和 `page_comments` 同一个形状：
 *  回复跟着它那栋楼走，不单独分页）。游标在这一份里就是顶层评论的下标 —— 真实实现
 *  是一对「时间戳 + uuid」，那是为了让翻页在时间戳相同时仍然有序；假数据不需要。
 *  游标带着值（而不是「第几页」）这一点是一样的：数到哪就是哪，中间删了也不会跳。 */
function threadPage(item: FeedbackDetail, after: string | null): { thread: FeedbackComment[]; next: string | null } {
  const tops = item.thread.filter((c) => c.parent_id === null)
  const start = Math.max(0, Number(after) || 0)
  const page = tops.slice(start, start + PROTO_THREAD_PAGE)
  const ids = new Set(page.map((c) => c.id))
  const thread = [...page, ...item.thread.filter((c) => c.parent_id !== null && ids.has(c.parent_id))]
  const more = start + PROTO_THREAD_PAGE < tops.length
  return { thread, next: more ? String(start + PROTO_THREAD_PAGE) : null }
}

/** 详情响应：正文是整条，评论只给一页。`comments` 仍然是**总数**（`row` 里按整条
 *  线程算的）—— 分页不许让卡片上那个计数变小，那正是这一版修掉的一个错。 */
function detailPage(item: FeedbackDetail, after: string | null): FeedbackDetail {
  const { thread, next } = threadPage(item, after)
  return { ...item, thread, thread_next_cursor: next }
}

/** 时间线：从提交到当前状态每一步都留一条，和真实实现一样是 append-only。 */
function ladderUpTo(status: FeedbackStatus, created: string): FeedbackDetail['timeline'] {
  const ladder: FeedbackStatus[] = ['received', 'in_progress', 'resolved', 'deployed']
  const end = ladder.indexOf(status)
  return ladder.slice(0, end + 1).map((step, index) => ({
    status: step,
    by_handle: index === 0 ? null : 'andy',
    at: created,
  }))
}

/** 一条评论。`reply_to_handle` / `likes` / `liked` / `can_delete` 这四列是这一版
 *  新加的（服务端算出来的），默认值集中写在这里而不是在每个字面量里重复一遍 ——
 *  真实实现只有一处（`services.comments_out`），fixture 这边抄五遍就等着漂。 */
function comment(spec: {
  id: string
  /** 折楼之后的父亲：顶层恒为 null，回复指向**顶层**那条（服务端折过）。 */
  parent?: string | null
  author: string
  authorIsAgent?: boolean
  body: string
  minutesAgo: number
  /** 只在这一条是回复、且它回的那条本身也是回复时有值。顶层不出现。 */
  replyTo?: string
  likes?: number
  liked?: boolean
  canDelete?: boolean
}): FeedbackComment {
  return {
    id: spec.id,
    parent_id: spec.parent ?? null,
    author_handle: spec.author,
    author_is_agent: spec.authorIsAgent ?? false,
    author_avatar_id: avatarOf(spec.author),
    body: spec.body,
    reply_to_handle: spec.replyTo ?? null,
    likes: spec.likes ?? 0,
    liked: spec.liked ?? false,
    // 预览里就是「我写的能删、别人的不能」：服务端的规则是作者本人或管理员，
    // 这里按同一个规则抄一份，别演成「谁都能删」。
    can_delete: spec.canDelete ?? spec.author === ME,
    // 这两个都由 `threadOf` 在整条线程拿齐之后补上：它们是「这一栋有多少条」和
    // 「下一页从哪开始」，单条评论自己算不出来。
    reply_count: 0,
    replies_next_cursor: null,
    created_at: ago(spec.minutesAgo),
  }
}

const HANDWRITTEN: FeedbackDetail[] = [
  row({
    id: 'fb-1042',
    display: 'FB-1042',
    kind: 'bug',
    title: '提交反馈时抽屉被手机键盘顶住',
    summary: '软键盘弹出来以后「提交」按钮留在键盘底下，点不到，只能先把键盘收起来。',
    status: 'in_progress',
    priority: 'high',
    author: 'cheese-c82aeb40',
    authorIsAgent: true,
    submittedBy: 'andy',
    assignee: 'andylizf',
    tags: ['移动端', '反馈'],
    supports: 7,
    supported: true,
    minutesAgo: 320,
    problem: '在手机上写反馈，写到第二行时软键盘把提交按钮盖住了。要提交得先点空白处收键盘，再找按钮。',
    why: '抽屉的高度用的是 100vh，软键盘弹出来时布局视口没有跟着缩，底部那一行就留在键盘底下了。',
    expectation: '键盘弹出来时抽屉自己让开，按钮跟着往上走。',
    whatHappened: '用户在话题里描述了一个问题，我顺着这条路径打开反馈抽屉想提一条，发现提交按钮点不到。',
    repro: '1. 手机打开反馈中心 → 2. 点「提交反馈」→ 3. 在正文里输入两行 → 4. 观察提交按钮的位置。',
    evidence: '截图里提交按钮的 y 坐标（612）大于可视区高度（596）。',
    environment: 'iOS 18 / Safari，视口 390×844',
    // 这一版新加的五种形态**全都摆出来**，摆不出来的话预览就没验到它：一条被赞过
    // 的顶层、一条自己赞过的回复（取消点赞那一半）、一条「回复 X」的楼内回复、一条
    // 别人的（删不掉，按钮不出现）、一栋回复多到折起来、一栋自己的楼（删除要问
    // 的那一句带上条数）。
    thread: [
      comment({
        id: 'c-1',
        author: 'andylizf',
        body: '复现了。`interactive-widget=resizes-content` 那条 meta 只在部分浏览器认，Safari 走的是 JS 那条路，抽屉那层没接。',
        minutesAgo: 280,
        likes: 3,
      }),
      comment({
        id: 'c-2',
        parent: 'c-1',
        author: ME,
        body: '那就把抽屉底部改成跟着 `dvh` 走，别再用 vh。',
        minutesAgo: 240,
        likes: 1,
        liked: true,
      }),
      comment({
        id: 'c-3',
        parent: 'c-1',
        author: 'andylizf',
        body: '`dvh` 在老一点的内核上也有坑，得留一个 `visualViewport` 的回退。',
        minutesAgo: 200,
        // 回的是楼里另一条回复：折楼之后只有这一列还记得它回的是谁。
        replyTo: ME,
        likes: 2,
      }),
      comment({
        id: 'c-4',
        parent: 'c-1',
        author: ME,
        body: '那两条路都留着，按 `visualViewport` 在不在挑。',
        minutesAgo: 150,
        replyTo: 'andylizf',
      }),
      comment({
        id: 'c-5',
        parent: 'c-1',
        author: 'andylizf',
        body: '我先按这个改，改完在这条下面回。',
        minutesAgo: 90,
      }),
      // 第二栋楼是**自己写的一条**，因为要让人看到「删掉这条评论，连同它下面的
      // 三条回复一起？」那一句。第一栋的楼主是别人，按钮根本不出现；而这一句的
      // 措辞正是要拍板的东西（只说「删掉这条评论」而实际删掉一栋楼，是在骗按
      // 按钮的人）。顺带让「两栋楼之间靠间距和缩进分块、不画分隔线」有第二栋
      // 可比 —— 只有一栋楼的时候，这条规则看不出来。
      comment({
        id: 'c-6',
        author: ME,
        body: '我也撞上过一次，是在横屏 + 外接键盘的时候。',
        minutesAgo: 60,
        likes: 1,
      }),
      comment({
        id: 'c-7',
        parent: 'c-6',
        author: 'chiruotong',
        body: '+1，躺着用的时候也复现了。',
        minutesAgo: 40,
        replyTo: ME,
      }),
      comment({
        id: 'c-8',
        parent: 'c-6',
        author: 'andylizf',
        body: '横屏那条是另一个原因（安全区没算进去），我单独开一条。',
        minutesAgo: 30,
      }),
      comment({
        id: 'c-9',
        parent: 'c-6',
        author: ME,
        body: '好，那就分开跟。',
        minutesAgo: 20,
      }),
      // 第三栋楼存在的唯一理由是**让「加载更多评论」出现**：假数据一页给 2 条顶层
      // 评论（`PROTO_THREAD_PAGE`），有三栋才翻得出第二页。它自己只有一条回复，
      // 顺手也摆出「一栋只有一两条时不折」的样子 —— 折起来的是回复，不是楼。
      comment({
        id: 'c-10',
        author: 'chiruotong',
        body: '另外提一句：抽屉里那个「选择文件」按不动，旁边还写着「上传还没接」——那是给我们看的，不是给用户看的。',
        minutesAgo: 10,
      }),
      comment({
        id: 'c-11',
        parent: 'c-10',
        author: ME,
        body: '已经拿掉了，附件是这一版之外的事。',
        minutesAgo: 5,
        replyTo: 'chiruotong',
      }),
      // 第 4~8 栋楼存在的理由只有一个：**让这一页明显长过一屏**。
      //
      // 评论框现在黏在评论区底部（`.fb-composer`，理由写在 FeedbackDetailPage.vue
      // 里），而「黏住」只在框的**自然位置掉到屏幕外**时才看得见 —— 框是评论区最后
      // 一个元素，前面那几栋楼不够高的话，它从一开始就在屏幕里，滚到哪儿都不动。
      // 上面三栋楼加正文和「现场」三段一共只有 759px 的滚动量，900px 的视口装得下，
      // 于是这一版最要做的那个改动在预览里一次都演不出来。多这几栋之后，点两下
      // 「加载更多评论」页面就够长，滚到半路能看见框贴在底下、正文还在屏幕外时不出现。
      //
      // 内容照这条反馈本身接着写，不另起一件事 —— 假数据里混进无关话题，看的人会
      // 以为是别的用例。
      comment({
        id: 'c-12',
        author: 'maxiaoyu',
        body: 'Android 上也撞上了，而且更别扭：键盘弹出来抽屉被顶上去一截，按钮跟着涨成两行，**第二行还是压在键盘底下**。',
        minutesAgo: 260,
        likes: 2,
      }),
      comment({
        id: 'c-13',
        parent: 'c-12',
        author: 'andylizf',
        body: '那是 `dvh` 算进去了、底部安全区没算。和横屏那条同一个改法，一起修。',
        minutesAgo: 235,
        replyTo: 'maxiaoyu',
      }),
      comment({
        id: 'c-14',
        author: 'pengwenbo',
        body: '补一个相关的：说明文字也被盖住半行，正好是「必填」那一句。读的人以为整块都可以不填。',
        minutesAgo: 180,
      }),
      comment({
        id: 'c-15',
        parent: 'c-14',
        author: 'cheese-c82aeb40',
        authorIsAgent: true,
        body: '同一个根因。抽屉里所有贴底的元素都得跟着可视区高度走，不只是提交按钮 —— 现在是一处一处调，改完整块一起验一遍更省事。',
        minutesAgo: 150,
        replyTo: 'pengwenbo',
      }),
      comment({
        id: 'c-16',
        author: 'caisongyang',
        body: '桌面端外接键盘的时候也复现了，窗口拉矮一点就出来。',
        minutesAgo: 120,
      }),
      comment({
        id: 'c-17',
        parent: 'c-16',
        author: ME,
        body: '桌面端这条我复现不出来，能贴一下窗口高度和浏览器吗？先按移动端改，桌面的另开一条跟。',
        minutesAgo: 95,
        replyTo: 'caisongyang',
      }),
      comment({
        id: 'c-18',
        author: 'ligan',
        body: '这条我等改完再来验。先在下面记一笔：改完把「提交反馈」那个按钮在键盘弹出时的位置也看一眼，别只修抽屉 —— 上一轮就是两处各写一遍、只修了一处。',
        minutesAgo: 45,
        likes: 1,
      }),
    ],
  }),
  row({
    id: 'fb-1041',
    display: 'FB-1041',
    kind: 'bug',
    title: '反馈中心在手机上没有分页入口',
    summary: '列表一页 50 条，超过以后没有「加载更多」，也没有页码。',
    status: 'in_progress',
    visibility: 'private',
    author: 'chiruotong',
    submittedBy: 'chiruotong',
    supports: 0,
    minutesAgo: 900,
    problem: '翻到底就没有了，一共多少条也看不出来。',
    expectation: '底部给一个「加载更多」，或者显示「已显示 50 / 132」。',
  }),
  row({
    id: 'fb-1040',
    display: 'FB-1040',
    kind: 'bug',
    title: '导出报表偶发 502',
    summary: '大报表导出时大约每五次有一次 502，重试就好。',
    status: 'received',
    priority: 'high',
    visibility: 'private',
    security: true,
    author: 'maxiaoyu',
    submittedBy: 'maxiaoyu',
    assignee: 'andy',
    supports: 2,
    minutesAgo: 1500,
    problem: '导出 3 万行以上的报表时，网关报 502。',
    repro: '选一个超过 3 万行的报表，点导出，连续试五次能撞上一次。',
    evidence: '网关日志里那几次都是 `upstream prematurely closed connection`。',
    thread: [],
    notes: [
      {
        id: 'n-1',
        author_handle: 'andy',
        author_avatar_id: avatarOf('andy'),
        body: '先别公开：里面可能带着别的项目的数据量信息。等定位完再看要不要合并回普通条目。',
        created_at: ago(1400),
      },
    ],
  }),
  row({
    id: 'fb-1039',
    display: 'FB-1039',
    kind: 'suggestion',
    title: '反馈列表想按支持数排序',
    summary: '现在按时间，人多的一条会沉下去。',
    // 一条走完四级、停在最后一格的样例：预览里要同时看得到「已修复」和「已上线」
    // 这两种收尾，否则只验证了一半的收尾样式。
    status: 'deployed',
    author: 'pengwenbo',
    submittedBy: 'pengwenbo',
    supports: 5,
    minutesAgo: 4300,
    problem: '支持是「我也遇到了」，按时间排的话它带着的那点信息就废了。',
    expectation: '「热门」那一栏能按支持数排。',
  }),
  row({
    id: 'fb-1038',
    display: 'FB-1038',
    kind: 'bug',
    title: '办完了的 bug 仍然占着列表第一屏',
    summary: '修完的 bug 还在最上面，找新问题得往下翻。',
    status: 'resolved',
    author: 'caisongyang',
    submittedBy: 'caisongyang',
    assignee: 'andylizf',
    supports: 1,
    minutesAgo: 6600,
    problem: '办完了的条目不该继续占着「全部」的第一屏。',
    expectation: '默认沉底。',
  }),
  // 下面这一批是**为管理台补的**：原来的五条里只有一条私密加安全、一条 agent，
  // 管理端四个栏位点进去有两栏几乎是空的 —— 那样一张截图看不出这个后台**在忙的时候**
  // 长什么样，也就没法判断重设计到底解不解决问题。**不编数字**：这几条和上面五条
  // 是同一份数据，栏位计数、统计块、空态全都从这一份算出来。
  row({
    id: 'fb-1037',
    display: 'FB-1037',
    kind: 'bug',
    title: '话题文档里的表格在深色模式下看不清',
    summary: '表头和正文的底色几乎一样，横线也看不见。',
    status: 'received',
    priority: 'high',
    author: 'ligan',
    submittedBy: 'ligan',
    supports: 4,
    minutesAgo: 180,
    problem: '深色模式下话题文档里的表格读不出来：表头是 `--fill`，正文也是 `--fill`，中间那条线在深色下基本看不见。',
    why: '文档正文走的是 markdown 渲染那一份样式，它没有跟着设计系统的深色块走。',
    expectation: '表头比正文略深一档，横线用 `--line-2`。',
  }),
  row({
    id: 'fb-1036',
    display: 'FB-1036',
    kind: 'suggestion',
    title: '希望话题能置顶几条',
    summary: '一个长期跑的话题，最重要的那条结论会被后来的对话顶走。',
    status: 'in_progress',
    author: 'chiruotong',
    submittedBy: 'chiruotong',
    assignee: 'andy',
    tags: ['对话', '组织'],
    supports: 6,
    minutesAgo: 700,
    problem: '话题里的结论散在对话里，新来的人要翻很久。',
    expectation: '能把某几条消息钉在话题顶部。',
  }),
  row({
    id: 'fb-1035',
    display: 'FB-1035',
    kind: 'bug',
    title: '定时巡检有时连着两轮都没跑',
    summary: '按小时排的巡检，日志里偶尔缺两轮，下一轮又正常。',
    status: 'received',
    author: 'cheese-71f0b2ad',
    authorIsAgent: true,
    submittedBy: 'andylizf',
    supports: 0,
    minutesAgo: 45,
    problem: '同一个话题的定时巡检，在 03:00 和 04:00 两轮里都没有产生记录，05:00 那轮正常。',
    whatHappened: '我在话题里按小时做巡检，翻记录时发现有两轮完全没有产物。',
    repro: '把巡检排成每小时一次，连着看 24 轮的记录。',
    evidence: '话题记录的 event 里 03:00 / 04:00 两轮不存在，前后两轮都在。',
    environment: '定时巡检 / 平台托管机器',
  }),
  row({
    id: 'fb-1034',
    display: 'FB-1034',
    kind: 'suggestion',
    title: '想把一个话题里的文件打包带走',
    summary: '话题工作区攒了一堆产物，想要一个按钮把它们打成一份。',
    status: 'received',
    visibility: 'private',
    author: 'maxiaoyu',
    submittedBy: 'maxiaoyu',
    supports: 3,
    minutesAgo: 2600,
    problem: '一次会话里产出的几份文档要交出去，只能一份一份下载。',
    expectation: '话题里给一个「打包下载」。',
  }),
  row({
    id: 'fb-1033',
    display: 'FB-1033',
    kind: 'bug',
    title: '私有部署的机器上工作区目录权限过宽',
    summary: '工作区目录对所有本机用户可读，里面可能有会话内容。',
    status: 'in_progress',
    priority: 'urgent',
    visibility: 'private',
    security: true,
    author: 'n1ctheboy',
    submittedBy: 'n1ctheboy',
    assignee: 'andy',
    supports: 1,
    minutesAgo: 340,
    problem: '装机器的时候工作区目录建成 0755，同机器上任何一个账号都能读里面的会话文件。',
    repro: '装完机器，`ls -ld ~/cheese/home/*/room`。',
    evidence: '`drwxr-xr-x` 加上目录属主是会话用户。',
    environment: '自托管机器 / Ubuntu 24.04',
    notes: [
      {
        id: 'n-2',
        author_handle: 'andy',
        author_avatar_id: avatarOf('andy'),
        body: '改成 0700；已经在跑的机器要出一条修好的命令，不能只改安装脚本。',
        created_at: ago(300),
      },
    ],
  }),
  row({
    id: 'fb-1032',
    display: 'FB-1032',
    kind: 'bug',
    title: '列表滚动到一半会跳回顶部',
    summary: '在反馈中心往下翻，点完一条再回来位置就没了。',
    status: 'resolved',
    author: 'pengwenbo',
    submittedBy: 'pengwenbo',
    assignee: 'andylizf',
    supports: 9,
    minutesAgo: 5200,
    problem: '从列表点进详情再返回，列表重新挂载，滚动位置丢掉。',
    expectation: '返回时停在原来的位置。',
  }),
  row({
    id: 'fb-1031',
    display: 'FB-1031',
    kind: 'bug',
    title: '提交按钮在窄屏上被一级导航盖住',
    summary: '手机上最下面一行按钮压在底部导航底下，点不到。',
    status: 'deployed',
    author: 'cheese-c82aeb40',
    authorIsAgent: true,
    submittedBy: 'wangchangxin',
    assignee: 'andy',
    supports: 2,
    minutesAgo: 12000,
    problem: '窄屏（<600px）下底部一级导航高 56px，抽屉底部的按钮没有给它让位。',
    repro: '把窗口宽度调到 390px，打开提交抽屉，看最下面那行按钮。',
    environment: 'Chrome / 视口 390×844',
  }),
  row({
    id: 'fb-1030',
    display: 'FB-1030',
    kind: 'suggestion',
    title: '反馈里想能贴一张图',
    summary: '说明问题的时候，一句话不如一张截图。',
    status: 'received',
    author: 'caisongyang',
    submittedBy: 'caisongyang',
    supports: 8,
    minutesAgo: 7000,
    problem: '只能写字，很多界面问题描述起来很费劲。',
    expectation: '能直接粘一张截图进去。',
  }),
]

/** 列表可见性。真实实现在 `services.may_see` 里判（提交者 ∪ 管理员 ∪ 提出它的房间），这里按预览身份简化：
 *  预览一律当管理员，所以私密条目原样显示，带着中性「私密」标签。 */
function visibleToMe(item: FeedbackCard): boolean {
  if (item.visibility === 'public') return true
  return IS_ADMIN || item.author_handle === ME || item.submitted_by_handle === ME
}

/** 补白行：让队列真的长过一屏。
 *
 *  **为什么要有**：上面那十三条在 620 高的视口里只溢出 24px，于是「表头常驻」
 *  「只有表格自己滚」「一屏十七行」这几条在预览里**一条都演不出来** —— 量出来是
 *  「滚动量足够了 = false」。四个栏位也各要有十几条，否则点进去三两条，看着像没做完。
 *
 *  **时间的铺法**（这一轮改的）：从今天铺到 **30 天前**，中间**故意留空几天**。
 *  两件事都是为了看板那张按天的折线：每一天都有数据的话，「没有数据的那天补 0」
 *  这条规则在预览里一次都验不到（量出来是「缺口 = 0 天」）。所以：
 *
 *  * 第 6 天故意整天空着（下面 `h: 143` 和 `h: 169` 之间跨过了这一整天）——
 *    「过去 7 天」这一档里就有一个 0，而 7 天是看板的默认档。
 *  * 11 天以外只落在 11 / 12 / 14 / 15 / 17 / 19 / 21 / 22 / 24 / 26 / 28 / 30 上，
 *    跳过的那些天（13、16、18、20、23、25、27、29…）在 30 天那一档里就是缺口。
 *
 *  **三种优先级都要有**（low / normal / high / urgent）：队列页的分诊靠它，
 *  少了 low 那一档，那一档的样式在预览里根本画不出来。
 *
 *  **不冒充真实工单**：正文比上面那几条短、没有评论线程、没有内部备注；id 从
 *  FB-1029 往下排。上面那十三条是照着真事写的，这下面是形状。 */
const FILLER: {
  kind: FeedbackKind
  title: string
  summary: string
  status: FeedbackStatus
  /** 优先级。不给就是 `normal`（`row()` 的默认值）。 */
  p?: FeedbackPriority
  author: string
  supports: number
  /** 多少小时前提的。 */
  h: number
  vis?: 'private'
  sec?: boolean
  agent?: boolean
  who?: string
}[] = [
  {
    kind: 'bug',
    title: '消息列表滚到底之后不再加载',
    summary: '翻到最后一页就不动了，得刷新整页才继续。',
    status: 'in_progress',
    p: 'normal',
    author: 'ligan',
    supports: 3,
    h: 30,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '深色模式下引用块和正文一个颜色',
    summary: '引用看不出来是引用，得对着格式工具栏猜。',
    status: 'received',
    p: 'low',
    author: 'chiruotong',
    supports: 1,
    h: 34,
  },
  {
    kind: 'suggestion',
    title: '话题列表想按最近活动排序',
    summary: '现在只能按创建时间，聊得最热的那几个沉在底下。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    supports: 4,
    h: 40,
    who: 'pengwenbo',
  },
  {
    kind: 'bug',
    title: '换头像之后要刷新才变',
    summary: '换完还是旧的那张，刷新一次才对。',
    status: 'resolved',
    p: 'low',
    author: 'caisongyang',
    supports: 2,
    h: 52,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '手机上弹起键盘后输入框跑到屏幕外',
    summary: '键盘一出来整页上移，光标看不见了。',
    status: 'in_progress',
    p: 'high',
    author: 'n1ctheboy',
    supports: 6,
    h: 58,
    who: 'andy',
  },
  {
    kind: 'other',
    title: '想一次导出话题里的全部文档',
    summary: '打包成 markdown 下载，现在只能一篇篇复制。',
    status: 'received',
    p: 'normal',
    author: 'pengwenbo',
    supports: 5,
    h: 66,
  },
  {
    kind: 'bug',
    title: '任务卡里的代码块没有复制按钮',
    summary: '每次都要手动选中一大段。',
    status: 'deployed',
    p: 'low',
    author: 'ligan',
    supports: 1,
    h: 72,
    who: 'andylizf',
  },
  {
    kind: 'suggestion',
    title: '侧栏的项目想能拖拽排序',
    summary: '项目一多就要从头找。',
    status: 'received',
    p: 'normal',
    author: 'chiruotong',
    supports: 3,
    h: 80,
  },
  {
    kind: 'bug',
    title: '点通知进去有时候是空页面',
    summary: '通知里带的那条链接偶尔指向已经不在的东西。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    supports: 2,
    h: 90,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '长文档写到大半会卡',
    summary: '三千字以上输入有明显延迟，一段字要等一下才出。',
    status: 'in_progress',
    p: 'high',
    author: 'caisongyang',
    supports: 7,
    h: 96,
    who: 'n1ctheboy',
  },
  {
    kind: 'suggestion',
    title: '想订阅某一条反馈',
    summary: '有动静的时候推给我，不想天天回来看。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 8,
    h: 104,
  },
  {
    kind: 'bug',
    title: '输入法组字时把拼音当查询发出去了',
    summary: '中文打字的时候列表一直闪，组完才稳。',
    status: 'resolved',
    p: 'low',
    author: 'chiruotong',
    supports: 4,
    h: 112,
    who: 'andy',
  },
  // ---- 缺口：`h: 112` 之后直接跳到 `h: 169` ----
  //
  // 上一档（`h: 112`）和这一档之间，**`[120h, 168h]` 这一段一条都没有**。一条反馈落在
  // 「6 天前那个 UTC 日」里的充要条件是它的 `h` 落在 `[120h, 168h]` —— 那个日子的两端
  // 是「今天已经过了 d 小时」的函数：它从 `120 + 24d` 小时前排到 `144 + 24d` 小时前，
  // d 在 `[0, 24)` 里取遍，并集正好是这一段。所以这一段空着 = **不管页面在一天里的
  // 哪个小时打开，7 天窗口里最早的那天都是 0**，折线上那个缺口是真的补出来的 0。
  //
  // 只按「h 差了多少」看会看错：`h: 143` 和 `h: 169` 差 26 小时，看着跨过了一整天，
  // 但页面在下午打开时它们是**同一天**里的两条。原来这里就是这么写的，缺口时有时无。
  {
    kind: 'other',
    title: '想看到每个项目用了多少额度',
    summary: '现在只有一个总数，不知道是哪个项目在烧。',
    status: 'received',
    p: 'normal',
    author: 'pengwenbo',
    supports: 6,
    h: 169,
  },
  {
    kind: 'bug',
    title: '表格里的数字没对齐',
    summary: '千分位之后列宽一直在跳。',
    status: 'deployed',
    p: 'low',
    author: 'maxiaoyu',
    supports: 1,
    h: 178,
    who: 'caisongyang',
  },
  {
    kind: 'suggestion',
    title: '评论里想能 @ 人',
    summary: '现在只能手打 handle，还得记住拼写。',
    status: 'received',
    p: 'normal',
    author: 'n1ctheboy',
    supports: 9,
    h: 180,
  },
  {
    kind: 'bug',
    title: '定时巡检连着两轮没跑',
    summary: '日志里干脆没有那一轮，只能等下一轮。',
    status: 'resolved',
    p: 'high',
    author: 'andy',
    supports: 5,
    h: 185,
    who: 'ligan',
  },
  {
    kind: 'bug',
    title: '从详情页复制代码会带上行号',
    summary: '粘到编辑器里还得一行行删。',
    status: 'received',
    p: 'low',
    author: 'caisongyang',
    supports: 2,
    h: 190,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '深色模式下透明图片是一块白',
    summary: '截图贴进来之后背景没跟着变。',
    status: 'in_progress',
    p: 'normal',
    author: 'pengwenbo',
    supports: 3,
    h: 195,
    who: 'andy',
  },
  {
    kind: 'suggestion',
    title: '机器列表想能按团队筛',
    summary: '几十台机器混在一起，找人借一台要翻半天。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 4,
    h: 200,
  },
  {
    kind: 'bug',
    title: '登出之后还能看见上一个账号的草稿',
    summary: '共享电脑上换人登录，抽屉里还是上一个人的字。',
    status: 'received',
    p: 'urgent',
    author: 'chiruotong',
    supports: 10,
    h: 205,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: 'agent 提的反馈在列表里看不出是 agent',
    summary: '得点进去看来源那一栏。',
    status: 'received',
    p: 'normal',
    author: 'cheese-c82aeb40',
    agent: true,
    supports: 2,
    h: 210,
  },
  {
    kind: 'suggestion',
    title: 'agent 想能自己认领一条反馈',
    summary: '现在只能在聊天里说一声让人去改。',
    status: 'received',
    p: 'normal',
    author: 'cheese-9f31d7c2',
    agent: true,
    supports: 3,
    h: 215,
  },
  {
    kind: 'bug',
    title: '私密反馈在管理端要一眼看得出来',
    summary: '和公开的挤在同一栏里长得一样。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    vis: 'private',
    supports: 1,
    h: 220,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '有一条写接口可以被匿名调用',
    summary: '没登录也能把它触发一遍。',
    status: 'in_progress',
    p: 'urgent',
    author: 'chiruotong',
    vis: 'private',
    sec: true,
    supports: 2,
    h: 230,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '日志里出现了会话凭证',
    summary: '排查的时候在前置机的日志里看到了。',
    status: 'received',
    p: 'high',
    author: 'n1ctheboy',
    vis: 'private',
    sec: true,
    supports: 4,
    h: 240,
  },
  // ---- 11~30 天：只有这一段才把窗口铺到 30 天前，跳过的天就是折线上的缺口 ----
  {
    kind: 'bug',
    title: '话题里的代码块打印时被截掉右边',
    summary: '长于一行的代码在导出 PDF 时右边被切掉。',
    status: 'resolved',
    p: 'high',
    author: 'pengwenbo',
    supports: 2,
    h: 264,
    who: 'andylizf',
  },
  {
    kind: 'suggestion',
    title: '机器列表想标出哪几台是平台托管的',
    summary: '自托管和托管混在一张表里，分不出来。',
    status: 'received',
    p: 'normal',
    author: 'chiruotong',
    supports: 4,
    h: 290,
  },
  {
    kind: 'bug',
    title: '任务卡上的验收按钮在窄屏上换行',
    summary: '390px 下按钮掉到第二行，和旁边那个挤在一起。',
    status: 'deployed',
    p: 'normal',
    author: 'caisongyang',
    supports: 1,
    h: 336,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '通知里的话题名被截成两行',
    summary: '一行放得下的名字被折成两行，列表跟着跳。',
    status: 'in_progress',
    p: 'low',
    author: 'maxiaoyu',
    supports: 3,
    h: 362,
    who: 'pengwenbo',
  },
  {
    kind: 'suggestion',
    title: '想给话题加一个「只看结论」的开关',
    summary: '结论散在对话里，只想把结论挑出来读。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 6,
    h: 410,
  },
  {
    kind: 'bug',
    title: '搜索结果里的话题点进去是空的',
    summary: '搜到的话题打得开，里面一条消息都没有。',
    status: 'resolved',
    p: 'high',
    author: 'n1ctheboy',
    supports: 2,
    h: 458,
    who: 'andy',
  },
  {
    kind: 'other',
    title: '想按周收到一份用量邮件',
    summary: '不想天天进来看，一周一封就够。',
    status: 'received',
    p: 'low',
    author: 'pengwenbo',
    supports: 5,
    h: 505,
  },
  {
    kind: 'bug',
    title: 'agent 的会话在侧栏里没有区分',
    summary: '我开的和 agent 开的排在一起，长得一样。',
    status: 'received',
    p: 'normal',
    author: 'cheese-4a1d90f3',
    agent: true,
    supports: 1,
    h: 530,
  },
  {
    kind: 'bug',
    title: '浅色模式下选中行的底色和 hover 分不开',
    summary: '指到哪儿和选中哪儿，看着是同一块颜色。',
    status: 'deployed',
    p: 'low',
    author: 'chiruotong',
    supports: 2,
    h: 578,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '导出 markdown 时表格丢了表头',
    summary: '导出来的表格第一行是数据，表头整行没了。',
    status: 'resolved',
    p: 'normal',
    author: 'caisongyang',
    vis: 'private',
    supports: 4,
    h: 626,
    who: 'andy',
  },
  {
    kind: 'suggestion',
    title: '想能一次把几条反馈标成已解决',
    summary: '分诊完一批要一条条点，手都点酸了。',
    status: 'in_progress',
    p: 'normal',
    author: 'ligan',
    supports: 3,
    h: 672,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '登录之后跳回上一条话题而不是首页',
    summary: '在别人机器上登录，落地的是别人上次看的那条话题。',
    status: 'deployed',
    p: 'high',
    author: 'maxiaoyu',
    supports: 7,
    h: 720,
    who: 'andylizf',
  },
]
const ROWS: FeedbackDetail[] = [
  ...HANDWRITTEN,
  ...FILLER.map((f, i) =>
    row({
      id: `fb-${1029 - i}`,
      display: `FB-${1029 - i}`,
      kind: f.kind,
      title: f.title,
      summary: f.summary,
      status: f.status,
      // 不传优先级就是 `normal`：`row()` 里那一个默认值是一处，不在每条上重写一遍。
      priority: f.p,
      author: f.author,
      authorIsAgent: f.agent,
      visibility: f.vis ?? 'public',
      security: f.sec ?? false,
      assignee: f.who,
      supports: f.supports,
      minutesAgo: f.h * 60,
      problem: f.summary,
    })
  ),
]

/** 反馈中心那份列表的可见性：后端 `PUBLIC_ONLY` = **公开且不是安全问题**，对谁都一样。
 *
 *  **管理员在反馈中心也看不到私密条目** —— 私密只在「我的反馈」和管理端出现。
 *  预览原来写的是「预览一律当管理员，所以私密原样显示」，那比真环境宽松，而它错在
 *  一个最容易被信的地方：照这个预览去判断，会得出「我提的私密反馈在反馈中心里看得见」，
 *  线上却是看不见的。 */
function inPublicList(item: FeedbackCard): boolean {
  return item.visibility === 'public' && !item.security
}

/** 「办完了」= 已修复 **和** 已上线，和 `repositories.CLOSED_STATUSES` 同一份口径。 */
const CLOSED: FeedbackStatus[] = ['resolved', 'deployed']

/** 栏位谓词。这是 `repositories._tab_where` 的镜像，**形状也照抄**：
 *  `sunk` 一处定义、三个栏位共用，免得这里写着写着就和后端分了叉 —— 预览存在的
 *  意义就是让人看后端的口径长什么样，它自己先不一致就白看了。
 *
 *  §8.23：只有**办完了的 bug** 沉底。办完了的建议是「团队决定做、并且做了」，
 *  仍然值得读；两者都在 `resolved` 栏里，所以四个数字不是一份划分。 */
function matchesTab(item: FeedbackCard, tab: string): boolean {
  const closed = CLOSED.includes(item.status)
  if (tab === 'resolved') return closed
  const sunk = !closed || item.kind !== 'bug'
  if (tab === 'active') return sunk && item.status === 'in_progress'
  // 「热门」这一栏是**近似**：后端按热度分（支持数按半衰期打折）比 `HOT_SCORE`，这里
  // 按原始支持数比 `HOT_MIN_ITEMS`。假数据的 `created_at` 都在几分钟内，两者只差在
  // 「几条刚发出来、票还少的」，而预览要给人看的是这一栏的样子，不是那个门槛的边界。
  if (tab === 'hot') return sunk && item.supports >= HOT_MIN_ITEMS
  return sunk
}

/** 搜索匹配的字段。**四列**，和 `repositories.matching(q)` 是同一份：标题、摘要、
 *  正文、作者。以前这里只有前两列，是「搜索扩到四列」那一批漏改的一处 —— 预览于是
 *  比真环境**搜得少**：按作者名搜、按正文里的词搜，真环境有结果、预览回空。这种
 *  偏差最坏的地方是它看着像「功能没做」。 */
function matchesQuery(item: FeedbackDetail, q: string): boolean {
  if (!q) return true
  const needle = q.toLowerCase()
  const fields = [item.title, item.summary, item.problem, item.author_handle]
  return fields.some((one) => (one ?? '').toLowerCase().includes(needle))
}

/** 「我的条目」：`author` / `submitted_by` / `assignee` 里有我。未读那条游标问的
 *  就是这批条目上的动静（`count_activity_since` 的 `mine`）。 */
function isMine(item: FeedbackCard): boolean {
  return item.author_handle === ME || item.submitted_by_handle === ME || item.assignee_handle === ME
}

/** 「我上次读到哪」——预览里是**一条游标**，不是一个常量。初始值取 8 小时前：
 *  它不是随手写的，是让下面那个数**从数据里数得出东西**的那个窗口（`fb-1042` 里
 *  别人写的评论都落在最近 5 小时里）。 */
const UNREAD_WINDOW_MS = 8 * 3_600_000
let lastReadMs = BASE_MS - UNREAD_WINDOW_MS

/** 未读：别人在我的条目上新写的评论和状态变迁，落在这条游标之后的**有多少条**。
 *
 *  这是 `repositories.count_activity_since` 的镜像，所以它**不是**写死的一个数：
 *  写死的话，按一下 `M`（`POST /feedback/read`）徽标不动 —— 而「未读数是死档」
 *  正是这一轮要修的那个 bug（`markRead()` 全仓零调用点）。
 *
 *  预览里那条时间线每一级都是 `andy` 推的（见 `ladderUpTo`），而我自己的动作
 *  不算我的新闻，所以这份数据里未读数**只由评论贡献**。真环境里另一条腿
 *  （别人推的状态）在这里是空的，不是我把它算漏了。 */
function unreadCount(): number {
  let n = 0
  for (const item of ROWS) {
    if (!isMine(item)) continue
    for (const c of item.thread) {
      if (c.author_handle !== ME && Date.parse(c.created_at) > lastReadMs) n += 1
    }
    for (const step of item.timeline) {
      if (step.by_handle && step.by_handle !== ME && Date.parse(step.at) > lastReadMs) n += 1
    }
  }
  return n
}

/** 管理端的「还没人管」：`assignee_handle` 为空、且**还没办完**的条目有几条。
 *
 *  这是 `repositories.unassigned_count` 的镜像，两处都和直觉不一样：
 *  **不受 `PUBLIC_ONLY` 收窄**（私密和安全那两栏也算 —— 分诊台问的正是这个数），
 *  而「办完了」是 `CLOSED` 两级（原来只减了 `resolved`，`deployed` 那种漏在里头，
 *  于是这个数比服务端大一点，而两个数各自看着都对）。 */
function unassignedCount(): number {
  return ROWS.filter((item) => !item.assignee_handle && !CLOSED.includes(item.status)).length
}

/** 计数与列表用**同一个**谓词算，免得预览里的 Tab 数字和点进去看到的条数对不上。
 *
 *  `deployed` 是 `public_counts` 里**另外**多报的一个数：`resolved` 那一栏装的是
 *  修复 + 上线这一对（口径不改），看板要的是两条线，所以另一条得单独给。
 *  `unread` / `unassigned` 是管理端那两个，口径写在上面各自的注释里。 */
function counts(): FeedbackCounts {
  const open = ROWS.filter(inPublicList)
  return {
    all: open.filter((item) => matchesTab(item, 'all')).length,
    hot: open.filter((item) => matchesTab(item, 'hot')).length,
    active: open.filter((item) => matchesTab(item, 'active')).length,
    resolved: open.filter((item) => matchesTab(item, 'resolved')).length,
    deployed: open.filter((item) => item.status === 'deployed').length,
    unread: unreadCount(),
    unassigned: unassignedCount(),
  }
}

/** 一页。**切页是服务端的事**（FastAPI 那两档是 `page_start` / `page_size`），
 *  假后端也得切：客户端只画这一页，不切的话「翻页」在预览里点得动、内容不动 ——
 *  而那种假数据看上去最像真的。默认 20 / 0 就是服务端那两个默认值。 */
function slicePage(url: URL, rows: FeedbackCard[]): FeedbackCard[] {
  const size = Number(url.searchParams.get('page_size'))
  const start = Number(url.searchParams.get('page_start'))
  const limit = Number.isFinite(size) && size >= 1 ? Math.floor(size) : 20
  const offset = Number.isFinite(start) && start >= 0 ? Math.floor(start) : 0
  return rows.slice(offset, offset + limit)
}

/** `since` 按**提交时间**收。 */
function createdSince(item: FeedbackCard, since: string | null): boolean {
  if (!since) return true
  const at = Date.parse(since)
  if (Number.isNaN(at)) return true
  return Date.parse(item.created_at) >= at
}

/** 这条反馈在窗口里**到过**这个状态 —— 问的是时间线，不是「现在是不是这个状态」。
 *  一条后来又被退回的条目照样算「解决过」，那正是分诊要看见的那一批
 *  （`repositories._reached_since`）。 */
function reachedSince(item: FeedbackDetail, status: FeedbackStatus, since: string | null): boolean {
  if (!since) return true
  const at = Date.parse(since)
  if (Number.isNaN(at)) return true
  return item.timeline.some((step) => step.status === status && Date.parse(step.at) >= at)
}

/** `FB-1042` → 1042。后端排的是 `display_no`（整数），不是字符串。 */
function displayNo(item: FeedbackCard): number {
  return Number(item.display_id.replace(/\D/g, '')) || 0
}

function sorted(list: FeedbackDetail[], sort = 'new'): FeedbackCard[] {
  // 真接口在后端排序，见 `backend/app/domain/feedback/repositories.py` 的 `_list_stmt`：
  // 默认口径是 `ORDER BY created_at DESC, display_no DESC`。`last_activity_at`
  // **不参与排序**（后端只把它当展示字段挂在卡上），这里排它是错的。
  //
  // `sort=supports`（管理台的「最热」）是那支语句里唯一一条非默认分支，三档照抄：
  // `count(supports) DESC, created_at DESC, display_no DESC`。**后两档不是装饰**：
  // 只按支持数排的话，两条同数的行谁在前是数据库说了算，页面上「换了排序但看着没变」
  // 和小批翻页时行重复/漏行都是它。
  //
  // 预览里必须真的按 `sort` 排，否则最热那一栏点了和没点一样，而**界面上看不出**
  // 是不是假后端没实现 —— 这正是这块假数据最容易骗过自己的地方。
  const byNew = (a: FeedbackCard, b: FeedbackCard) =>
    a.created_at === b.created_at ? displayNo(b) - displayNo(a) : a.created_at < b.created_at ? 1 : -1
  const copy = [...list]
  if (sort === 'supports') {
    return copy.sort((a, b) => (a.supports === b.supports ? byNew(a, b) : b.supports - a.supports))
  }
  return copy.sort(byNew)
}

/** 一页列表。`total` 是**筛完之后**的总数（不是这一页几条）—— 分页要它才知道
 *  还有没有下一页；`data` 只给这一页。 */
function paged(rows: FeedbackDetail[], url: URL, sort: string) {
  const ordered = sorted(rows, sort)
  return { data: slicePage(url, ordered), total: ordered.length, counts: counts() }
}

function listPage(url: URL, tab: string): { data: FeedbackCard[]; total: number; counts: FeedbackCounts } {
  const q = url.searchParams.get('q') ?? ''
  const sort = url.searchParams.get('sort') ?? 'new'
  // 公开那条路由**不校验** `tab` / `sort`（服务端 `list_public` 对不认识的词悄悄
  // 退回默认档，见那一段的 docstring）—— 这是那条路由自己的口径，和管理端不同。
  return paged(
    ROWS.filter(inPublicList)
      .filter((item) => matchesTab(item, tab))
      .filter((item) => matchesQuery(item, q)),
    url,
    sort
  )
}

/** 管理端那一页。query 的口径照抄 `repositories.list_admin` + `services.list_admin`：
 *
 *  * 四个栏位（`ADMIN_TABS`）。`private` 是「私密**且不是安全问题**」——
 *    少后面那半句，页面上就会出现「这一栏说有它、安全那一栏说没有它」的行。
 *  * `sort` 两档（`SORTS`），不认识的由调用方拒掉（400），和 `tab` 同一条规矩。
 *  * 三个时间下界**口径不一样**，别当成一组：`since` 收的是**提交时间**，
 *    `resolved_since` / `deployed_since` 收的是「窗口内**到过**那个状态」。
 *    三个都不给就是全量那一页（这一组参数是纯增量）。
 *  * `assignee` 按指派人收（服务端 `Feedback.assignee_handle == assignee`）。
 *
 *  `counts` 和列表**不是一套谓词**（服务端也这样）：那一份是四个公开栏位的数
 *  加上管理端那两个，不随这里的筛选变。改它才是错的 —— 看板上的数字会跟着
 *  输入框里打的字跳。 */
function adminPage(
  url: URL,
  tab: string,
  sort: string
): { data: FeedbackCard[]; total: number; counts: FeedbackCounts } {
  const q = url.searchParams.get('q') ?? ''
  const assignee = url.searchParams.get('assignee')
  let rows = ROWS.filter((item) => matchesAdminTab(item, tab))
  if (assignee) rows = rows.filter((item) => item.assignee_handle === assignee)
  rows = rows.filter((item) => matchesQuery(item, q))
  rows = rows.filter((item) => createdSince(item, url.searchParams.get('since')))
  rows = rows.filter((item) => reachedSince(item, 'resolved', url.searchParams.get('resolved_since')))
  rows = rows.filter((item) => reachedSince(item, 'deployed', url.searchParams.get('deployed_since')))
  return paged(rows, url, sort)
}

/** 管理端四个栏位。`public` 那一档用的是 `PUBLIC_ONLY`（公开**且不是安全问题**），
 *  和反馈中心同一份谓词 —— 少一个条件就是「管理端说这条是公开的、公开列表里却没有
 *  它」的来源。另外三档见 `list_admin`。 */
function matchesAdminTab(item: FeedbackCard, tab: string): boolean {
  switch (tab) {
    case 'private':
      return item.visibility === 'private' && !item.security
    case 'agent':
      return item.author_is_agent
    case 'security':
      return item.security
    default:
      return inPublicList(item)
  }
}

/** 那两个名单，和服务端 `services.ADMIN_TABS` / `SORTS` 同一份。`ADMIN_TABS` 直接取
 *  `META.admin_tabs`：那一份 `/feedback/meta` 已经发给了页面，两个地方各写一份的话，
 *  页面上画的栏位和后台认的栏位会是两份名单（多一栏时后者直接把请求打成 400）。 */
const ADMIN_TABS = META.admin_tabs
const SORTS = ['new', 'supports']

/* ---- 看板（`/admin/stats/*`）的三块 -------------------------------
 *
 * 服务端**一个分类一条路由**（`backend/app/api/routes/admin_stats.py`），形状钉死在
 * `domain/platform_stats/services.py` 里，这里逐字照抄：`days` 原样回显、`series`
 * 恒有 `days` 行、**最早的一天在前**、缺的那天是 0。
 *
 * `series` 和 `counts` 都从 `ROWS` 里数出来，不另编一套：看板上的「7 日新增」和队列
 * 里那 7 天真有的条目必须是同一批 —— 各数一遍的症状是「看板和反馈管理页对同一天给出
 * 两个数」，而两边各自看着都对。
 *
 * 三块里有两块（用量、平台）在 `ROWS` 里没有对应数据，那两块是**编的**；编也得编得
 * 自洽，怎么来的写在各自那一段上面。
 */

/** 窗口天数。服务端签名是 `ge=1 le=90`、默认 7 —— 三个数各有一处理由（那个模块的
 *  docstring）：默认 7 是页头上那句「过去 7 天」，上界 90 是这个读本身的成本，下界 1 是
 *  「0 天画不出一条折线」。缺省、不是个数、小于 1 时退到 7，超过上界时截到 90 ——
 *  这两条**不是服务端的行为**（那边是 422 / 400），是预览里「不让人因为一个参数打不开
 *  页面」的取舍；页面自己从不这么问。 */
function windowDays(url: URL): number {
  const asked = Number(url.searchParams.get('days'))
  if (!Number.isFinite(asked) || asked < 1) return 7
  return Math.min(90, Math.floor(asked))
}

/** 一个 UTC 日的开始。窗口按 **UTC 的天**切，和 `windows.utc_day_window` 同一把尺子
 *  —— 按本地时区切的话，`days=7` 会跨进第八天的一小段，而 series 里没有它的位置：
 *  那一段被算进总量却不出现在图上，两个数对不上而两边各自都「对」。 */
function utcDayStart(ms: number): number {
  const at = new Date(ms)
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate())
}

/** `YYYY-MM-DD` —— `dense_series` 里 `date.isoformat()` 的那个形状。 */
function dayKey(ms: number): string {
  return new Date(utcDayStart(ms)).toISOString().slice(0, 10)
}

/** 窗口里的日子：**最早在前、长度恒等于 `days`**。补 0 的判据只能是这一份 ——
 *  按「有数据的那几天」补的话，7 天的线会画成 5 天，而且看不出来（断点处是一条平滑的
 *  线，不是一段空白）。 */
function readDays(days: number): string[] {
  const since = utcDayStart(BASE_MS) - (days - 1) * 86_400_000
  return Array.from({ length: days }, (_, i) => dayKey(since + i * 86_400_000))
}

/** 把一个 `日期 → 数` 的稀疏表铺成 `days` 行，缺的那天写 0。列名由调用方点名（和
 *  `dense_series` 一样，不猜）：猜错的表现是一栏全是 0，而没有任何一处会报错。
 *
 *  行类型里 `date` 是**例外**，所以它单独写出来而不是并进索引签名：写成
 *  `{date: string} & Record<string, number>` 的话 `date` 会同时是 string 和 number
 *  （交集等于 never），字面量 `{date}` 当场就不满足它 —— 类型上说得通、代码里建不出来
 *  的那种形状。 */
function dense(
  days: number,
  columns: Record<string, Map<string, number>>
): Array<{ date: string; [column: string]: number | string }> {
  return readDays(days).map((date) => {
    const row: { date: string; [column: string]: number | string } = { date }
    for (const name of Object.keys(columns)) row[name] = columns[name].get(date) ?? 0
    return row
  })
}

/** `日期 → 那一天有几条反馈`（按 id 去重）。 */
function markDay(table: Map<string, Set<string>>, day: string, id: string): void {
  const ids = table.get(day) ?? new Set<string>()
  ids.add(id)
  table.set(day, ids)
}

function dayCounts(table: Map<string, Set<string>>): Map<string, number> {
  const out = new Map<string, number>()
  for (const [day, ids] of table) out.set(day, ids.size)
  return out
}

/** 窗口内按天新建的反馈数。窗口外那几天落在 `readDays` 之外的键上，`dense` 不读它们
 *  —— 和 SQL 那句 `created_at >= since AND < until` 是同一件事。 */
function createdDays(): Map<string, number> {
  const table = new Map<string, Set<string>>()
  for (const item of ROWS) markDay(table, dayKey(Date.parse(item.created_at)), item.id)
  return dayCounts(table)
}

/** 「那一天有几条反馈**到过**这个状态」。
 *
 *  问的是**时间线**，不是「现在是不是这个状态」（`_reached_since` / `reached_series`）：
 *  一条后来又被退回的条目照样算「那天解决过」，而那正是分诊要看见的一批。
 *
 *  同一个状态在时间线上可以有第二行（改了又改回来），服务端数的是
 *  `count(distinct feedback_id)` —— 「几条反馈」而不是「到过几次」，所以这里按 id 去重：
 *  假后端把「到过 2 次」画成 2，图上就比真环境高一格，而那种偏差没人看得出来。 */
function reachedDays(status: FeedbackStatus): Map<string, number> {
  const table = new Map<string, Set<string>>()
  for (const item of ROWS) {
    for (const step of item.timeline) {
      if (step.status === status) markDay(table, dayKey(Date.parse(step.at)), item.id)
    }
  }
  return dayCounts(table)
}

/** 反馈那一块：栏位计数 + 按天的新增 / 解决 / 上线。
 *
 *  `counts` 是**全量口径**（不收窗口），`series` 才是窗口内的 —— 页面上「现在有多少」
 *  和「这七天怎么变的」是两个问题。七个键和 `PlatformStatsService.feedback` 那一个
 *  推导式一样（五个栏位数 + 未读 + 未指派）。 */
function feedbackStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const c = counts()
  return {
    days,
    counts: {
      all: c.all,
      hot: c.hot,
      active: c.active,
      resolved: c.resolved,
      deployed: c.deployed,
      unread: c.unread,
      unassigned: c.unassigned,
    },
    series: dense(days, {
      created: createdDays(),
      resolved: reachedDays('resolved'),
      deployed: reachedDays('deployed'),
    }),
  }
}

/* ---- 用量 ----------------------------------------------------------------
 *
 * 这一块 `ROWS` 里没有对应数据（那是一份反馈，一行用量也没有），所以是**编的**。
 * 编得自洽的四条：
 *
 *  * `totals` 就是 `series` 那几列各自的和，不是另取一个数 —— 图上的柱子和卡片上的
 *    合计对不上，是这一块最容易被信以为真的假。
 *  * `cost_usd` 由 token 按一个单价算出来（`USD_PER_MTOK`），不是随手编一个钱数：
 *    「几百万 token 配 $0.00」正是这一块最容易出的那种错。
 *  * `unpriced_tokens` 是其中**算不出价钱**的那一份（订阅按月计费的行 `cost_usd = 0`
 *    意思是「没有价」，不是免费），它从 `cost_usd` 的分母里扣掉。
 *  * **周日恒为 0**：每个 7 天窗口里正好有一个周日，所以「缺的天补 0」这件事在
 *    **每一条**窗口里都看得见，而不是碰巧有个空档。
 *
 * 同一天的数字只由**日期**决定（`hashDay`）：假数据也得能对着一张截图复现，刷新一次
 * 变一个数是最坏的一种假。 */

/** 一百万个 token 三美元 —— 真环境订阅与按量混着，这个数落在「几美元一千万」那个
 *  量级上就够看图了。 */
const USD_PER_MTOK = 3

/** 一天一个数：FNV-1a 折出来的。判据里**没有** `Date.now()`。 */
function hashDay(seed: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash
}

/** 0 = 周日。和窗口同一把尺子（UTC 的天）。 */
function weekdayOf(day: string): number {
  return new Date(`${day}T00:00:00Z`).getUTCDay()
}

function usageOfDay(day: string): {
  tokens: number
  calls: number
  cost_usd: number
  unpriced_tokens: number
} {
  // 一行一次调用（`/v1/messages`），一天六到十七次。周日 0。
  const calls = weekdayOf(day) === 0 ? 0 : 6 + (hashDay(day) % 12)
  // 一次调用两三千 token，和真环境一个量级。
  const tokens = Math.round((calls * (2400 + (hashDay(`t${day}`) % 2000))) / 100) * 100
  // 订阅计费那一份占 2%~6%。这个数只为让「有几百万 token 算不出价钱」在界面上说得
  // 出来（`unpriced_tokens()`），所以四舍五入到百位就够了。
  const unpriced_tokens = Math.round((tokens * (2 + (hashDay(`u${day}`) % 5))) / 100 / 100) * 100
  const usd = ((tokens - unpriced_tokens) / 1_000_000) * USD_PER_MTOK
  return { tokens, calls, cost_usd: Number(usd.toFixed(4)), unpriced_tokens }
}

/** 窗口内那几天的用量，加它们各自的和。`usageStats` 和老路径那条别名都从这里读，
 *  所以「合计」在两个形状里是同一个数。 */
function usageWindow(days: number) {
  const rows = readDays(days).map((date) => ({ date, ...usageOfDay(date) }))
  const sum = (key: 'tokens' | 'calls' | 'cost_usd' | 'unpriced_tokens'): number =>
    rows.reduce((acc, row) => acc + row[key], 0)
  return {
    rows,
    tokens: sum('tokens'),
    calls: sum('calls'),
    cost: Number(sum('cost_usd').toFixed(4)),
    unpriced: sum('unpriced_tokens'),
  }
}

/** 柱状图上那几根柱子。名字是**仓库里真有的**（每条后面的出处就是它出现的地方），
 *  不是六个看起来像项目的字符串。`project_id` 是固定的假 uuid —— 预览里没有项目这一
 *  层，柱子点不开是预览的边界，不是这一份数据的错。 */
const TOP_PROJECTS: { id: string; name: string }[] = [
  // 「知是平台后端」：`backend/tests/integration/test_artifact_is_the_repository.py`
  // 里建的项目名。
  { id: '4a1c0f6e-7b52-4d9a-9c31-2f8d5a0b7e11', name: '知是平台后端' },
  // 「cheese 自建」：`backend/scripts/device_selfhost_smoke.py` 与
  // `machine_chain_check.py` 里那个默认的项目名（`device_capability_setup.py` 也有）。
  { id: 'b7d2e904-3c18-4a67-8f25-6e0c1d9a4b52', name: 'cheese 自建' },
  // 「看板项目」：`backend/tests/integration/test_admin_stats.py` 里那一条。
  { id: '0c93f2a1-5d47-4e88-b1a6-7c4b2e0f9d38', name: '看板项目' },
  // 「创新项目入驻 2026 秋」：`backend/scripts/sim_real.py` / `seed_demo.py`
  // 里那条演示项目。
  { id: 'e58b1a37-9f24-4c10-8d73-1a6e0b5c7f92', name: '创新项目入驻 2026 秋' },
  // 「AI 课程推荐系统」：同上。
  { id: '3f7c8d20-6a51-4b93-9e08-5d2c7a1f04b6', name: 'AI 课程推荐系统' },
  // 「cheese_status」：`backend/tests/unit/test_cheese_cli.py` 里那个项目名。
  { id: 'd21e6b85-4c03-4f7a-a5b9-8e1d3c60f7a4', name: 'cheese_status' },
  // 「结题报告」：后端用例里的项目名。
  { id: '7b0a4e6c-2d95-4e3b-8c17-a9f5d2b803e1', name: '结题报告' },
]

/** 前六名各占多少，**最后一名吃掉余数** —— 这样加起来正好等于合计（差一百 token 就是
 *  「柱子和合计对不上」，而那是这一块唯一会被核对的地方）。份额本身是编的，但它是这份
 *  假数据里唯一一处编的地方，而且可核对。 */
const TOP_PROJECT_WEIGHTS = [0.24, 0.18, 0.13, 0.11, 0.09, 0.07]

function splitShares(total: number, weights: number[]): number[] {
  const out: number[] = []
  let rest = total
  for (const weight of weights) {
    const part = Math.round((total * weight) / 100) * 100
    out.push(part)
    rest -= part
  }
  out.push(rest)
  return out
}

/** 窗口内用量最高的几个项目（`UsageRepository.top_projects` 那一支）。 */
function topProjects(tokens: number, unpriced: number): Record<string, unknown>[] {
  const tokensBy = splitShares(tokens, TOP_PROJECT_WEIGHTS)
  const unpricedBy = splitShares(unpriced, TOP_PROJECT_WEIGHTS)
  return TOP_PROJECTS.map((project, i) => ({
    project_id: project.id,
    name: project.name,
    tokens: tokensBy[i],
    // 算得出价钱的那一份才进 `cost_usd`：和 `unpriced_tokens` 是同一条口径，柱子上的
    // 钱和卡片上的钱要能对得上。
    cost_usd: Number((((tokensBy[i] - unpricedBy[i]) / 1_000_000) * USD_PER_MTOK).toFixed(4)),
  }))
}

/** 用量那一块：窗口内的总量、按天序列、最花钱的几个项目。 */
function usageStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const bucket = usageWindow(days)
  return {
    days,
    // 上面那三列的和，就是这一份 totals —— 这一块没有第二个读法。
    totals: {
      tokens: bucket.tokens,
      calls: bucket.calls,
      cost_usd: bucket.cost,
      unpriced_tokens: bucket.unpriced,
    },
    series: bucket.rows.map(({ date, tokens, calls, cost_usd }) => ({ date, tokens, calls, cost_usd })),
    top_projects: topProjects(bucket.tokens, bucket.unpriced),
  }
}

/* ---- 平台 ----------------------------------------------------------------
 *
 * `people.total` 是本文件成员管理那一段注释里那个数（1199 个账号，`admin_members.py`
 * 的注释记着同一个数），`admins` 是**当场数出来的**名单长度（根 ∪ 页面上加的）——
 * 看板另记一个数的话，症状是「看板说 9 个、成员管理页列出来 8 个」。
 *
 * `new` 是窗口内新增的账号数，也就等于 `series` 里那些 `created` 的和：一个给总量、
 * 一个给形状，两个数来自同一份读数。周日恒为 0（和用量同一条理由）。
 *
 * `machines` 四个数是四张台账的**存量**，不是在线数（在线状态住在进程内存里，库里
 * 没有那一列，理由在 `platform_stats/repositories.py` 的模块 docstring 里）。预览里
 * 没有设备页，这四个数**没有对照物**，所以它们只是四个量级上说得过去的常数，别当读数用。
 */
const ACCOUNT_TOTAL = 1199
const MACHINE_STOCK = {
  devices: 7,
  hosted_devices: 5,
  warm_machines: 3,
  project_machines: 2,
}

function signupsOfDay(day: string): number {
  return weekdayOf(day) === 0 ? 0 : hashDay(`s${day}`) % 4
}

/** 平台那一块：账号的存量与新增、设备台账的存量。 */
function platformStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const created = new Map<string, number>()
  for (const day of readDays(days)) created.set(day, signupsOfDay(day))
  return {
    days,
    people: {
      total: ACCOUNT_TOTAL,
      new: [...created.values()].reduce((acc, n) => acc + n, 0),
      // 名单是**活的**：成员管理页加了人，这里下一次就是新数。
      admins: ROOT_ADMINS.length + ADDED_ADMINS.filter((row) => !ROOT_ADMINS.includes(row.handle)).length,
      series: dense(days, { created }),
    },
    machines: { ...MACHINE_STOCK },
  }
}

let nextId = 1043

/* ---- 成员管理（`/admin/members`）的假数据 ---- */

/** 部署配置里那份根名单。页面上**删不掉**，所以它是一份常量，不受 POST/DELETE 影响
 *  —— 预览里那几行没有「移出」按钮，正是要看的那个形态。 */
const ROOT_ADMINS = ['andy', 'wangchangxin']

/** 页面上加的那一份。形状照服务端的两块（`root` / `added`）分开给，不合成一个带标记
 *  的数组：两块的**操作权不一样**，分组规则不该由前端再定一份。 */
const ADDED_ADMINS: { handle: string; added_by_handle: string; created_at: string }[] = [
  { handle: 'pengwenbo', added_by_handle: 'andy', created_at: ago(60 * 24 * 3) },
]

/** 加人那个搜索框里的账号池。真环境是 1199 个账号、搜索在 SQL 里；预览里这几个人
 *  够点出「搜 handle 或昵称都能搜到」「已经是管理员的置灰」这两种形态。 */
const ACCOUNTS: { handle: string; nickname: string }[] = [
  { handle: 'andy', nickname: 'andy' },
  { handle: 'andylizf', nickname: 'andylizf' },
  { handle: 'caisongyang', nickname: '蔡松洋' },
  { handle: 'chiruotong', nickname: '池若彤' },
  { handle: 'ligan', nickname: '李甘' },
  { handle: 'maxiaoyu', nickname: '马霄宇' },
  { handle: 'n1ctheboy', nickname: '李甘-nictheboy' },
  { handle: 'pengwenbo', nickname: '彭文博' },
  { handle: 'wangchangxin', nickname: '符露夀' },
]

function adminRoster() {
  return { root: [...ROOT_ADMINS], added: ADDED_ADMINS.map((row) => ({ ...row })) }
}

function isAdminHandle(handle: string): boolean {
  return ROOT_ADMINS.includes(handle) || ADDED_ADMINS.some((row) => row.handle === handle)
}

/** 假数据的一条答复。`data` 是正常路径；`missing` / `refused` / `forbidden` 是三种
 *  「这件事不能发生」，在 `installPreviewFetch` 里分别翻成 404、412、403 —— 预览要看
 *  到真接口的错误面，否则「按钮点下去没反应」这类问题只有在真机上才现形。
 *
 *  412 和 403 是**两件事**，不能合成一个：412 是「这件事现在不能做，别重试」（已经
 *  办完了、今天配额用完了），403 是「你没有这个权限」。合成一个的话，预览里删别人的
 *  评论会得到一句「别重试」，而服务端给的是「只能删除自己的评论」—— 读的人会去查一
 *  个不存在的原因。（目前只有删评论用 403，`ForbiddenError`。）
 *
 *  `invalid`（400）和 `conflict`（409）是同一条纪律的延续，都是名单那两个接口带来的：
 *  「这个 handle 平台上没有」是 400、「他是部署配置里的根管理员」是 409。这两个不能
 *  并进 `refused` 的 412 —— 412 对客户端说的是「这件事现在不能做，别重试」，而这两条
 *  说的是「你请求里那个名字有问题」，改个名字就能成。并进去的话，预览里给根管理员
 *  按删除会得到一句「别重试」，人就会去查一个不存在的重试开关。 */
type MockReply =
  | { data: unknown }
  | { missing: true }
  | { refused: string }
  | { forbidden: string }
  | { invalid: string }
  | { conflict: string }
  | undefined

/** 提交、支持、评论这些写操作在预览里**真的改内存里的那份数据**：点一下按钮能看见
 *  列表变化，而不是弹一个「预览模式下不可用」。它们是预览，但不该是死的。 */
function routes(url: URL, method: string, body: unknown): MockReply {
  const path = url.pathname.replace(/^\/api/, '')
  const payload = (body ?? {}) as Record<string, never> & Record<string, unknown>

  if (path === '/feedback/meta' && method === 'GET') return { data: META }
  if (path === '/feedback/counts' && method === 'GET') return { data: counts() }
  if (path === '/feedback/read' && method === 'POST') {
    // 游标推到**此刻**，不是「这一条」：`markRead` 的语义是「我全看过了」，所以之后
    // 再问 `counts.unread` 是 0（store 本地也这么做，见那边的注释）。原来这里只回一个
    // 时间戳、不动游标 —— 于是按 `M` 之后徽标被本地归零，重新拉一次计数又跳回 12，
    // 而这件事在界面上看不出是假后端没实现。
    lastReadMs = BASE_MS
    return { data: { last_read_at: new Date(lastReadMs).toISOString() } }
  }
  if (path === '/feedback' && method === 'GET') return { data: listPage(url, url.searchParams.get('tab') ?? 'all') }
  if (path === '/feedback/mine' && method === 'GET') {
    const mine = ROWS.filter((item) => item.author_handle === ME || item.submitted_by_handle === ME)
    return { data: { data: sorted(mine), total: mine.length, counts: counts() } }
  }
  if (path === '/feedback' && method === 'POST') return { data: create(payload as unknown as FeedbackCreateBody) }

  // --- 看板（`/admin/stats/*`）---------------------------------------------
  //
  // 三条路由一个分类（`backend/app/api/routes/admin_stats.py`），切到哪一类才拉哪一类。
  // 这一组的前缀不是 `/admin/feedback`，所以不会被下面那条动态段吃掉。
  //
  // 这里以前还多一条**老路径** `/admin/feedback/stats` 的替身，注释里写着「看板那一页
  // 还没换过来」。那是个教训：它把真环境里的 **400** 盖住了 —— 前端指着一条已经没有的
  // 路由，`stats` 落进 `/admin/feedback/{id}` 被当成一个 uuid 解析，预览一切正常、dev 上
  // 是「看板加载失败」。**替身只该照抄服务端真实存在的东西**；一条为了「两种前端版本都
  // 不报警」而留的别名，代价是发现不了其中一种版本是坏的。
  const stats = /^\/admin\/stats\/([a-z]+)$/.exec(path)
  if (stats && method === 'GET') {
    if (stats[1] === 'feedback') return { data: feedbackStats(url) }
    if (stats[1] === 'usage') return { data: usageStats(url) }
    if (stats[1] === 'platform') return { data: platformStats(url) }
    // 分类只有三个，别的没有对应的路由 —— 服务端那是 404，不是「空数据」。
    return { missing: true }
  }

  const adminList = /^\/admin\/feedback$/.exec(path)
  if (adminList && method === 'GET') {
    const tab = url.searchParams.get('tab') ?? 'public'
    const sort = url.searchParams.get('sort') ?? 'new'
    // 不认识的 `tab` / `sort` 报 **400**，不是悄悄退回默认档（`services.list_admin` 那
    // 两处 `BadRequestError`，原话照抄 —— 页面把服务端那句直接显示出来）。管理端猜错
    // 栏位会让人以为「这条反馈不见了」，而它其实在隔壁那一栏；排序更硬：那一页的答案
    // 就是顺序，答成另一种排序等于用同一个标题回答了另一个问题。
    if (!ADMIN_TABS.includes(tab)) return { invalid: `未知的管理视图：${tab}` }
    if (!SORTS.includes(sort)) return { invalid: `未知的排序：${sort}` }
    return { data: adminPage(url, tab, sort) }
  }

  // --- 成员管理（`/admin/members`）----------------------------------------
  //
  // 三条写操作**真的改这份假名单**：加完一个人，右边那两块要当场变（同提交反馈、
  // 支持那条口径 —— 预览是给人看的，但不该是死的）。
  if (path === '/admin/admins' && method === 'GET') return { data: adminRoster() }
  if (path === '/admin/users' && method === 'GET') {
    const q = (url.searchParams.get('q') ?? '').trim().toLowerCase()
    const limit = Number(url.searchParams.get('limit') ?? 20)
    const items = ACCOUNTS.filter(
      (one) => one.handle.toLowerCase().includes(q) || one.nickname.toLowerCase().includes(q)
    )
      .slice(0, limit)
      .map((one) => ({
        ...one,
        avatar_id: avatarOf(one.handle),
        already_admin: isAdminHandle(one.handle),
      }))
    return { data: { items } }
  }
  if (path === '/admin/admins' && method === 'POST') {
    const wanted = String(payload.handle ?? '').trim()
    // 三条拒绝和服务端同一个**类别**，因为页面把服务端那句原话直接显示出来：
    // 预览里给一个别的说法，等于让「按下去看到什么」在预览和真机上不一样。
    if (!wanted) return { invalid: '要加的人不能是空的' }
    if (ROOT_ADMINS.includes(wanted)) {
      return { conflict: `${wanted} 是部署配置里的根管理员，不用在页面上加` }
    }
    if (!ACCOUNTS.some((one) => one.handle === wanted)) {
      return { invalid: `平台里没有 handle 是 ${wanted} 的账号` }
    }
    const created = !isAdminHandle(wanted)
    if (created) {
      ADDED_ADMINS.push({ handle: wanted, added_by_handle: ME, created_at: new Date().toISOString() })
    }
    return { data: { ...adminRoster(), created } }
  }

  const adminRemove = /^\/admin\/admins\/([^/]+)$/.exec(path)
  if (adminRemove && method === 'DELETE') {
    const target = decodeURIComponent(adminRemove[1])
    // 根删不掉：**409，不是静默不动**。页面上那几行本来就没有删除按钮，真按到了
    // 说明两边对不上，那就该说出来（`AdminService.remove_admin` 那条口径）。
    if (ROOT_ADMINS.includes(target)) {
      return { conflict: `${target} 是部署配置里的根管理员，页面上删不掉 —— 改配置要有服务器权限` }
    }
    const at = ADDED_ADMINS.findIndex((one) => one.handle === target)
    const removed = at >= 0
    if (removed) ADDED_ADMINS.splice(at, 1)
    return { data: { ...adminRoster(), removed } }
  }

  const detail = /^\/feedback\/([^/]+)$/.exec(path)
  if (detail && method === 'GET') {
    const item = find(detail[1])
    return { data: item && detailPage(item, url.searchParams.get('after')) }
  }
  const adminDetail = /^\/admin\/feedback\/([^/]+)$/.exec(path)
  if (adminDetail && method === 'GET') {
    const item = find(adminDetail[1])
    return { data: item && detailPage(item, url.searchParams.get('after')) }
  }

  const supports = /^\/feedback\/([^/]+)\/supports$/.exec(path)
  if (supports && (method === 'POST' || method === 'DELETE')) {
    const item = find(supports[1])
    if (!item) return { missing: true }
    if (method === 'POST' && CLOSED.includes(item.status)) {
      // 412，不是 404：反馈看得见，不能发生的是这个动作（`services.support`）。
      // 预览里也照此拒绝 —— 一个前端挡住但预览放行的按钮，正好是「看起来能点、
      // 真机上点了报错」的那类 bug，预览存在的意义就是别让它到那一步。
      return { refused: '这条反馈已经办完了，不再接受支持' }
    }
    item.supports += method === 'POST' ? 1 : -1
    item.supported = method === 'POST'
    return { data: { count: item.supports, supported: item.supported } }
  }

  const comments = /^\/feedback\/([^/]+)\/comments$/.exec(path)
  if (comments && method === 'GET') {
    const item = find(comments[1])
    if (!item) return { missing: true }
    // 楼内那一页：这一份假数据一次把一栋楼的回复给全了，所以 `replies_next_cursor`
    // 恒为 null、这个分支走不到。真走到了（比如以后给某栋楼造更多回复）回一页空的，
    // 而不是把已经给过的那几条再发一遍 —— 那会在屏幕上变成一人两条。
    if (url.searchParams.get('parent_id')) return { data: { items: [], next_cursor: null } }
    const { thread, next } = threadPage(item, url.searchParams.get('after'))
    return { data: { items: thread, next_cursor: next } }
  }
  if (comments && method === 'POST') {
    const item = find(comments[1])
    if (!item) return { missing: true }
    // 折楼和服务端同一处规则：回一条回复时 `parent_id` 落成那栋楼的顶层，而
    // `reply_to_handle` 只在「回的对象本身是回复」时记下来。预览里少折这一层，
    // 画出来就是三层楼 —— 而真实服务端造不出那个形状。
    const target = item.thread.find((c) => c.id === payload.parent_id)
    const created = comment({
      id: `c-${(item.thread.length + 10).toString()}`,
      parent: target ? target.parent_id ?? target.id : null,
      author: ME,
      body: String(payload.body ?? ''),
      minutesAgo: 0,
      replyTo: target?.parent_id ? target.author_handle : undefined,
    })
    item.thread = [...item.thread, created]
    item.comments += 1
    // 新回复要把它那一栋的**总数**也加一。客户端在本地做同一件事（`addComment`），
    // 但那是为了让眼前这一屏对得上；这里改的是「数据本身」，下次重新拉详情时
    // 才不会回退成少一条。
    const top = item.thread.find((c) => c.id === created.parent_id)
    if (top) top.reply_count += 1
    return { data: created }
  }

  const commentLike = /^\/feedback\/([^/]+)\/comments\/([^/]+)\/likes$/.exec(path)
  if (commentLike) {
    const item = find(commentLike[1])
    const target = item?.thread.find((c) => c.id === commentLike[2])
    if (!item || !target) return { missing: true }
    const wanted = method === 'POST'
    // 和 `toggleSupport` 那份一样：**回的是写完之后的服务端计数**，不是本地 ±1。
    // 同一个人重复 POST/DELETE 不叠加，这也正是唯一约束在做的事。
    if (target.liked !== wanted) {
      target.liked = wanted
      target.likes += wanted ? 1 : -1
    }
    return { data: { count: target.likes, liked: target.liked } }
  }

  const oneReport = /^\/feedback\/([^/]+)$/.exec(path)
  if (oneReport && method === 'DELETE') {
    const item = find(oneReport[1])
    if (!item) return { missing: true }
    // 服务端两条路分得很清楚：看不见 → 404，看得见但删不掉 → 403。预览里假后端的
    // 世界只有一个人（没有登录态），所以能走到这里的都判为「能删」；`can_delete`
    // 那半由页面自己按数据画按钮，和真机同一套。
    if (!item.can_delete) return { forbidden: '只能删除自己提交的反馈' }
    // 软删的**可观察那一半**：它从每一份列表里消失（真机上由服务端的读侧过滤完成）。
    ROWS.splice(ROWS.indexOf(item), 1)
    return { data: { deleted: true } }
  }

  const oneComment = /^\/feedback\/([^/]+)\/comments\/([^/]+)$/.exec(path)
  if (oneComment && method === 'DELETE') {
    const item = find(oneComment[1])
    if (!item) return { missing: true }
    const id = oneComment[2]
    if (!item.thread.some((c) => c.id === id && c.can_delete)) {
      // 「看得见但删不掉」（`may_delete_comment`），所以是 403 而不是 404，原话照抄
      // 服务端的 `ForbiddenError`。预览里也照此拒绝 —— 一个前端挡住而预览放行的
      // 按钮，正是「看着能点、真机上点了报错」那类 bug。
      return { forbidden: '只能删除自己的评论' }
    }
    // 级联：删一条顶层评论，它那一栋的回复跟着一起走（服务端同一个形状）。
    const goneRows = item.thread.filter((c) => c.id === id || c.parent_id === id)
    const gone = new Set(goneRows.map((c) => c.id))
    const kept = item.thread.filter((c) => !gone.has(c.id))
    item.comments -= gone.size
    item.thread = kept
    // 删掉的那几条回复，如果它那一栋还在（删的是一条楼内回复），那一栋的总数要跟着
    // 减：那个数是「展开更多 N 条回复」上写的字，少了这一步，删完之后按钮上写的
    // 数字和展开出来能看到的条数就对不上了。（`goneRows` 是删之前那一份 —— 上面
    // 已经把 `item.thread` 换成 `kept` 了，回头再按 id 找是找不到的。）
    for (const removed of goneRows) {
      if (!removed.parent_id) continue
      const top = kept.find((c) => c.id === removed.parent_id)
      if (top) top.reply_count = Math.max(0, top.reply_count - 1)
    }
    return { data: { ok: true } }
  }

  const status = /^\/admin\/feedback\/([^/]+)\/status$/.exec(path)
  if (status && method === 'POST') {
    const item = find(status[1])
    if (!item) return { missing: true }
    const wanted = payload.status as FeedbackStatus
    // 设成**同一个**状态是幂等的（`services.set_status`），不写第二条时间线：相隔一秒的
    // 两条一模一样的记录，读起来像历史出了 bug。按 `3`（已上线）再按一次是最常见的
    // 那种重复，而预览里那第二行是看得见的。
    if (item.status !== wanted) {
      item.status = wanted
      item.last_activity_at = new Date(BASE_MS).toISOString()
      item.timeline = [...item.timeline, { status: wanted, by_handle: ME, at: item.last_activity_at }]
    }
    return { data: item }
  }

  const notes = /^\/admin\/feedback\/([^/]+)\/notes$/.exec(path)
  if (notes && method === 'POST') {
    const item = find(notes[1])
    if (!item) return { missing: true }
    item.notes = [
      ...item.notes,
      {
        id: `n-${item.notes.length + 10}`,
        author_handle: ME,
        author_avatar_id: avatarOf(ME),
        body: String(payload.body ?? ''),
        created_at: ago(0),
      },
    ]
    return { data: item }
  }

  const patch = /^\/admin\/feedback\/([^/]+)$/.exec(path)
  if (patch && method === 'PATCH') {
    const item = find(patch[1])
    if (!item) return { missing: true }
    if (payload.priority) item.priority = payload.priority as FeedbackPriority
    if (payload.assignee_handle !== undefined) {
      // 空串是**清掉**指派人（`services.patch_admin` 那句 `or None`）—— 提交框里把人
      // 删掉发的就是一个空串，`?? null` 那种写法会把它存成一个空 handle，界面上看着像
      // 「指派给了一个没有名字的人」。`undefined` 才是「这次请求里没提到它」。
      item.assignee_handle = (payload.assignee_handle as string | null) || null
    }
    if (payload.security !== undefined && Boolean(payload.security) !== item.security) {
      item.security = Boolean(payload.security)
      // 标成安全问题是一次**路由决定**：提的人应当看得见，而且从这一刻起同事看不见它
      // 了。两件事从行本身都看不出来，所以进时间线 —— 和 `patch_admin` 一样，记的是
      // **当时**的状态（改安全标记不改状态）。
      item.timeline = [...item.timeline, { status: item.status, by_handle: ME, at: new Date(BASE_MS).toISOString() }]
    }
    return { data: item }
  }

  return undefined
}

function find(id: string): FeedbackDetail | undefined {
  return ROWS.find((item) => item.id === id)
}

function create(body: FeedbackCreateBody): FeedbackDetail {
  const id = `fb-${nextId}`
  const created = row({
    id,
    display: `FB-${nextId}`,
    kind: body.kind,
    title: body.title || '（无标题）',
    summary: body.summary || body.problem || '',
    status: 'received',
    visibility: body.visibility ?? 'public',
    author: ME,
    submittedBy: ME,
    supports: 0,
    minutesAgo: 0,
    problem: body.problem ?? '',
    whatHappened: body.what_happened ?? undefined,
    repro: body.repro ?? undefined,
    evidence: body.evidence ?? undefined,
    environment: body.environment ?? undefined,
  })
  nextId += 1
  ROWS.unshift(created)
  return created
}

/** 把 `/api/*` 上反馈的那几条路由接到假数据上。**只拦 `/api/`**：图标、字体那些
 *  请求照旧走真正的网络栈。 */
export function installPreviewFetch(): void {
  const real = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    if (!url.pathname.startsWith('/api/')) return real(input as RequestInfo, init)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = null
    if (typeof init?.body === 'string' && init.body) {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = null
      }
    }
    const hit = routes(url, method, body)
    if (hit === undefined) {
      // 走到这里说明页面调了一个这里没写的接口。预览里它不该发生；真发生了，
      // 报出来比在界面上留一个没有原因的空列表好。
      console.warn('[preview] 没有假数据的请求', method, url.pathname)
      return envelope(null)
    }
    if ('missing' in hit) return envelope(null, 404, '这条反馈打不开')
    if ('refused' in hit) return envelope(null, 412, hit.refused)
    if ('forbidden' in hit) return envelope(null, 403, hit.forbidden)
    if ('invalid' in hit) return envelope(null, 400, hit.invalid)
    if ('conflict' in hit) return envelope(null, 409, hit.conflict)
    return envelope(hit.data)
  }
}

function envelope(data: unknown, code = 200, message = 'ok'): Response {
  return new Response(JSON.stringify({ code, message, data }), {
    status: code === 200 ? 200 : code,
    headers: { 'content-type': 'application/json' },
  })
}
