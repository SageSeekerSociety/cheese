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
/** 「热门」的门槛，与 `GET /feedback/meta` 的 `hot_supports` 同一个数。 */
const HOT_SUPPORTS = 5
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
  hot_supports: HOT_SUPPORTS,
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

const ROWS: FeedbackDetail[] = [
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
    minutesAgo: 8600,
    problem: '办完了的条目不该继续占着「全部」的第一屏。',
    expectation: '默认沉底。',
  }),
]

/** 列表可见性。真实实现在 `services.may_see` 里判（提交者 ∪ 管理员），这里按预览身份简化：
 *  预览一律当管理员，所以私密条目原样显示，带着中性「私密」标签。 */
function visibleToMe(item: FeedbackCard): boolean {
  if (item.visibility === 'public') return true
  return IS_ADMIN || item.author_handle === ME || item.submitted_by_handle === ME
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
  if (tab === 'hot') return sunk && item.supports >= HOT_SUPPORTS
  return sunk
}

function matchesQuery(item: FeedbackCard, q: string): boolean {
  if (!q) return true
  const needle = q.toLowerCase()
  return item.title.toLowerCase().includes(needle) || item.summary.toLowerCase().includes(needle)
}

/** 计数与列表用**同一个**谓词算，免得预览里的 Tab 数字和点进去看到的条数对不上。 */
function counts(): FeedbackCounts {
  const open = ROWS.filter(visibleToMe)
  return {
    all: open.filter((item) => matchesTab(item, 'all')).length,
    hot: open.filter((item) => matchesTab(item, 'hot')).length,
    active: open.filter((item) => matchesTab(item, 'active')).length,
    resolved: open.filter((item) => matchesTab(item, 'resolved')).length,
    unread: 2,
    unassigned: open.filter((item) => !item.assignee_handle && item.status !== 'resolved').length,
  }
}

/** `FB-1042` → 1042。后端排的是 `display_no`（整数），不是字符串。 */
function displayNo(item: FeedbackCard): number {
  return Number(item.display_id.replace(/\D/g, '')) || 0
}

function sorted(list: FeedbackDetail[]): FeedbackCard[] {
  // 真接口在后端排序，见 `backend/app/domain/feedback/repositories.py` 的 `_list_stmt`：
  // 默认口径是 `ORDER BY created_at DESC, display_no DESC`。前端从来不传 `sort`，
  // 所以这条就是实际口径。`last_activity_at` **不参与排序**（后端只把它当展示字段挂在卡上），
  // 这里排它是错的；改成 `created_at` 再比一遍编号兜底（同一秒提交的两条才有这个需要）。
  return [...list].sort((a, b) =>
    a.created_at === b.created_at ? displayNo(b) - displayNo(a) : a.created_at < b.created_at ? 1 : -1
  )
}

function listPage(url: URL, tab: string): { data: FeedbackCard[]; total: number; counts: FeedbackCounts } {
  const q = url.searchParams.get('q') ?? ''
  const data = sorted(
    ROWS.filter(visibleToMe)
      .filter((item) => matchesTab(item, tab))
      .filter((item) => matchesQuery(item, q))
  )
  return { data, total: data.length, counts: counts() }
}

function adminPage(url: URL, tab: string): { data: FeedbackCard[]; total: number; counts: FeedbackCounts } {
  const q = url.searchParams.get('q') ?? ''
  const pick = (item: FeedbackCard): boolean => {
    switch (tab) {
      case 'private':
        return item.visibility === 'private'
      case 'agent':
        return item.author_is_agent
      case 'security':
        return item.security
      default:
        return item.visibility === 'public'
    }
  }
  const data = sorted(ROWS.filter(pick).filter((item) => matchesQuery(item, q)))
  return { data, total: data.length, counts: counts() }
}

let nextId = 1043

/** 假数据的一条答复。`data` 是正常路径；`missing` / `refused` / `forbidden` 是三种
 *  「这件事不能发生」，在 `installPreviewFetch` 里分别翻成 404、412、403 —— 预览要看
 *  到真接口的错误面，否则「按钮点下去没反应」这类问题只有在真机上才现形。
 *
 *  412 和 403 是**两件事**，不能合成一个：412 是「这件事现在不能做，别重试」（已经
 *  办完了、今天配额用完了），403 是「你没有这个权限」。合成一个的话，预览里删别人的
 *  评论会得到一句「别重试」，而服务端给的是「只能删除自己的评论」—— 读的人会去查一
 *  个不存在的原因。（目前只有删评论用 403，`ForbiddenError`。） */
type MockReply = { data: unknown } | { missing: true } | { refused: string } | { forbidden: string } | undefined

/** 提交、支持、评论这些写操作在预览里**真的改内存里的那份数据**：点一下按钮能看见
 *  列表变化，而不是弹一个「预览模式下不可用」。它们是预览，但不该是死的。 */
function routes(url: URL, method: string, body: unknown): MockReply {
  const path = url.pathname.replace(/^\/api/, '')
  const payload = (body ?? {}) as Record<string, never> & Record<string, unknown>

  if (path === '/feedback/meta' && method === 'GET') return { data: META }
  if (path === '/feedback/counts' && method === 'GET') return { data: counts() }
  if (path === '/feedback/read' && method === 'POST') return { data: { last_read_at: new Date(BASE_MS).toISOString() } }
  if (path === '/feedback' && method === 'GET') return { data: listPage(url, url.searchParams.get('tab') ?? 'all') }
  if (path === '/feedback/mine' && method === 'GET') {
    const mine = ROWS.filter((item) => item.author_handle === ME || item.submitted_by_handle === ME)
    return { data: { data: sorted(mine), total: mine.length, counts: counts() } }
  }
  if (path === '/feedback' && method === 'POST') return { data: create(payload as unknown as FeedbackCreateBody) }

  const adminList = /^\/admin\/feedback$/.exec(path)
  if (adminList && method === 'GET') return { data: adminPage(url, url.searchParams.get('tab') ?? 'public') }

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
    item.status = payload.status as FeedbackStatus
    item.last_activity_at = new Date(BASE_MS).toISOString()
    item.timeline = [...item.timeline, { status: item.status, by_handle: ME, at: item.last_activity_at }]
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
    if (payload.assignee_handle !== undefined) item.assignee_handle = (payload.assignee_handle as string | null) ?? null
    if (payload.security !== undefined) item.security = Boolean(payload.security)
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
    return envelope(hit.data)
  }
}

function envelope(data: unknown, code = 200, message = 'ok'): Response {
  return new Response(JSON.stringify({ code, message, data }), {
    status: code === 200 ? 200 : code,
    headers: { 'content-type': 'application/json' },
  })
}
