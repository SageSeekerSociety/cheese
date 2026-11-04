/**
 * proto-feedback-fixtures/list.ts — 列表的读侧：可见性、栏位、搜索、排序、分页与那几个
 * 计数，加上「我读到哪」这条游标。
 *
 * 每一条谓词都是后端某一处的镜像（注释里各自点了名）。口径写错的话，预览里 Tab 上的数
 * 和点进去看到的条数会对不上 —— 那是这块假数据最容易骗过自己的地方。
 */
import type { FeedbackCard, FeedbackCounts, FeedbackDetail, FeedbackStatus } from '@/cx_types'

import { ROWS } from './rows'
import { BASE_MS, HOT_MIN_ITEMS, IS_ADMIN, ME, META } from './support'

/** 列表可见性。真实实现在 `services.may_see` 里判（提交者 ∪ 管理员 ∪ 提出它的房间），这里按预览身份简化：
 *  预览一律当管理员，所以私密条目原样显示，带着中性「私密」标签。 */
function visibleToMe(item: FeedbackCard): boolean {
  if (item.visibility === 'public') return true
  return IS_ADMIN || item.author_handle === ME || item.submitted_by_handle === ME
}

/** 反馈中心那份列表的可见性：后端 `PUBLIC_ONLY` = **公开且不是安全问题**，对谁都一样。
 *
 *  **管理员在反馈中心也看不到私密条目** —— 私密只在「我的反馈」和管理端出现。
 *  预览原来写的是「预览一律当管理员，所以私密原样显示」，那比真环境宽松，而它错在
 *  一个最容易被信的地方：照这个预览去判断，会得出「我提的私密反馈在反馈中心里看得见」，
 *  线上却是看不见的。 */
function inPublicList(item: FeedbackCard): boolean {
  return item.visibility === 'public' && !item.security
}

/** 「办完了」= 已修复、已上线、不修复，和 `repositories.CLOSED_STATUSES` 同一份口径。 */
export const CLOSED: FeedbackStatus[] = ['resolved', 'deployed', 'declined']

/** 栏位谓词。这是 `repositories._tab_where` 的镜像，**形状也照抄**：
 *  免得这里写着写着就和后端分了叉 —— 预览存在的意义就是让人看后端的口径长什么样，
 *  它自己先不一致就白看了。
 *
 *  `all` 和 `hot` 都不按状态收窄（2026-10-28 撤掉了「已办完的 bug 沉底」那条：
 *  「全部」曾经比「已完成」少，因为它把办完的 Bug 拿掉了）。只有 `resolved` 有
 *  自己的规则，装的是三种结局的并集，所以三个数字不是一份划分。 */
function matchesTab(item: FeedbackCard, tab: string): boolean {
  if (tab === 'resolved') return CLOSED.includes(item.status)
  // 「热门」这一栏是**近似**：后端按热度分（支持数按半衰期打折）比 `HOT_SCORE`，这里
  // 按原始支持数比 `HOT_MIN_ITEMS`。假数据的 `created_at` 都在几分钟内，两者只差在
  // 「几条刚发出来、票还少的」，而预览要给人看的是这一栏的样子，不是那个门槛的边界。
  if (tab === 'hot') return item.supports >= HOT_MIN_ITEMS
  return true
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
/** `POST /feedback/read`：游标推到**此刻**（预览里没有登录态，「我」就是全部的读者），
 *  返回推完之后的那一刻。游标是列表这一侧的私事，所以它唯一的写门开在这里，路由不改
 *  一个 import 不进来的 `let`。 */
export function markRead(): string {
  lastReadMs = BASE_MS
  return new Date(lastReadMs).toISOString()
}

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
export function counts(): FeedbackCounts {
  const open = ROWS.filter(inPublicList)
  return {
    all: open.filter((item) => matchesTab(item, 'all')).length,
    hot: open.filter((item) => matchesTab(item, 'hot')).length,
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

export function sorted(list: FeedbackDetail[], sort = 'new'): FeedbackCard[] {
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

export function listPage(url: URL, tab: string): { data: FeedbackCard[]; total: number; counts: FeedbackCounts } {
  const q = url.searchParams.get('q') ?? ''
  const sort = url.searchParams.get('sort') ?? 'new'
  // 公开那条路由**不校验** `tab` / `sort`（服务端 `list_public` 对不认识的词悄悄
  // 退回默认档，见那一段的 docstring）—— 这是那条路由自己的口径，和管理端不同。
  //
  // 四个筛选（作者 / 类型 / 状态 / 起始时间）照抄 `repositories.list_public`：**叠在
  // 栏位之上**，不是替换它。假后端也得筛 —— 不筛的话预览里那排控件点了没反应，
  // 而预览正是拿来看这类东西的地方。
  const author = url.searchParams.get('author')
  const kind = url.searchParams.get('kind')
  const status = url.searchParams.get('status')
  const since = url.searchParams.get('since')
  return paged(
    ROWS.filter(inPublicList)
      .filter((item) => matchesTab(item, tab))
      .filter((item) => matchesQuery(item, q))
      .filter((item) => author === null || item.author_handle === author)
      .filter((item) => kind === null || item.kind === kind)
      .filter((item) => status === null || item.status === status)
      .filter((item) => since === null || item.created_at >= since),
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
export function adminPage(
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
export const ADMIN_TABS = META.admin_tabs
export const SORTS = ['new', 'supports']
