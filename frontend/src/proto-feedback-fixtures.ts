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
 * 已解决的 bug（默认沉底）、支持数过门槛的（进「热门」）、带安全标记的（管理端第四栏）。
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

/** 相对时间按**打开页面的那一刻**算：预览是拿来看「长什么样」的，把一个日期写死，
 *  过几天再打开就全是「8 天前」，而那不是任何人的真实体验。 */
const BASE_MS = Date.now()
function ago(minutes: number): string {
  return new Date(BASE_MS - minutes * 60_000).toISOString()
}

const META: FeedbackMeta = {
  kinds: ['bug', 'suggestion', 'other'],
  statuses: ['received', 'triaging', 'planned', 'in_progress', 'resolved'],
  priorities: ['low', 'normal', 'high', 'urgent'],
  visibilities: ['public', 'private'],
  status_ladder: ['received', 'triaging', 'planned', 'in_progress', 'resolved'],
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
    submitted_by_handle: spec.submittedBy ?? null,
    assignee_handle: spec.assignee ?? null,
    tags: spec.tags ?? [],
    supports: spec.supports,
    comments: spec.thread?.length ?? 0,
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
    thread: spec.thread ?? [],
    notes: spec.notes ?? [],
  }
}

/** 时间线：从提交到当前状态每一步都留一条，和真实实现一样是 append-only。 */
function ladderUpTo(status: FeedbackStatus, created: string): FeedbackDetail['timeline'] {
  const ladder: FeedbackStatus[] = ['received', 'triaging', 'planned', 'in_progress', 'resolved']
  const end = ladder.indexOf(status)
  return ladder.slice(0, end + 1).map((step, index) => ({
    status: step,
    by_handle: index === 0 ? null : 'andy',
    at: created,
  }))
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
    thread: [
      {
        id: 'c-1',
        parent_id: null,
        author_handle: 'andylizf',
        author_is_agent: false,
        body: '复现了。`interactive-widget=resizes-content` 那条 meta 只在部分浏览器认，Safari 走的是 JS 那条路，抽屉那层没接。',
        created_at: ago(280),
      },
      {
        id: 'c-2',
        parent_id: 'c-1',
        author_handle: ME,
        author_is_agent: false,
        body: '那就把抽屉底部改成跟着 `dvh` 走，别再用 vh。',
        created_at: ago(240),
      },
    ],
  }),
  row({
    id: 'fb-1041',
    display: 'FB-1041',
    kind: 'bug',
    title: '反馈中心在手机上没有分页入口',
    summary: '列表一页 50 条，超过以后没有「加载更多」，也没有页码。',
    status: 'triaging',
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
    status: 'resolved',
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
    title: '已解决的 bug 仍然占着列表第一屏',
    summary: '修完的 bug 还在最上面，找新问题得往下翻。',
    status: 'resolved',
    author: 'caisongyang',
    submittedBy: 'caisongyang',
    assignee: 'andylizf',
    supports: 1,
    minutesAgo: 8600,
    problem: '已解决的条目不该继续占着「全部」的第一屏。',
    expectation: '默认沉底。',
  }),
]

/** 列表可见性。真实实现在 `services.may_see` 里判（提交者 ∪ 管理员），这里按预览身份简化：
 *  预览一律当管理员，所以私密条目原样显示，带着中性「私密」标签。 */
function visibleToMe(item: FeedbackCard): boolean {
  if (item.visibility === 'public') return true
  return IS_ADMIN || item.author_handle === ME || item.submitted_by_handle === ME
}

function matchesTab(item: FeedbackCard, tab: string): boolean {
  switch (tab) {
    case 'hot':
      return item.supports >= HOT_SUPPORTS
    case 'active':
      return item.status !== 'resolved'
    case 'resolved':
      return item.status === 'resolved'
    default:
      // §8.23：默认口径只沉底**已解决的 bug**，建议和其他的已解决项照常显示。
      return !(item.kind === 'bug' && item.status === 'resolved')
  }
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

/** 提交、支持、评论这些写操作在预览里**真的改内存里的那份数据**：点一下按钮能看见
 *  列表变化，而不是弹一个「预览模式下不可用」。它们是预览，但不该是死的。 */
function routes(url: URL, method: string, body: unknown): { data: unknown } | { missing: true } | undefined {
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
  if (detail && method === 'GET') return { data: find(detail[1]) }
  const adminDetail = /^\/admin\/feedback\/([^/]+)$/.exec(path)
  if (adminDetail && method === 'GET') return { data: find(adminDetail[1]) }

  const supports = /^\/feedback\/([^/]+)\/supports$/.exec(path)
  if (supports && (method === 'POST' || method === 'DELETE')) {
    const item = find(supports[1])
    if (!item) return { missing: true }
    item.supports += method === 'POST' ? 1 : -1
    item.supported = method === 'POST'
    return { data: { count: item.supports, supported: item.supported } }
  }

  const comments = /^\/feedback\/([^/]+)\/comments$/.exec(path)
  if (comments && method === 'POST') {
    const item = find(comments[1])
    if (!item) return { missing: true }
    const created: FeedbackComment = {
      id: `c-${(item.thread.length + 10).toString()}`,
      parent_id: (payload.parent_id as string | null) ?? null,
      author_handle: ME,
      author_is_agent: false,
      body: String(payload.body ?? ''),
      created_at: new Date(BASE_MS).toISOString(),
    }
    item.thread = [...item.thread, created]
    item.comments += 1
    return { data: created }
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
      { id: `n-${item.notes.length + 10}`, author_handle: ME, body: String(payload.body ?? ''), created_at: ago(0) },
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
    return envelope(hit.data)
  }
}

function envelope(data: unknown, code = 200, message = 'ok'): Response {
  return new Response(JSON.stringify({ code, message, data }), {
    status: code === 200 ? 200 : code,
    headers: { 'content-type': 'application/json' },
  })
}
