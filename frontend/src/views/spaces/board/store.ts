/**
 * 空间新界面的数据层 —— **全部来自真接口**。
 *
 * 原型那份 `src/proto-board/store.ts` 把状态放在内存里自己演，因为原型要讲的是一条
 * 新流程（谁都能发题、自己出的自己审），不是某个接口的返回形状。这一份不是原型：
 * 每一个数字都从 `TasksApi` / `SpacesApi` 来，能写的地方（过审、驳回）也真的写回去。
 *
 * 它是**模块级单例**（和原型一样）而不是 pinia store：这一棵路由下面同时只开一个
 * 空间，页面之间靠 `loadBoard(spaceId)` 换空间，没有必要为此多一层 store 容器。
 *
 * 已知的映射损失，写在这里免得后面有人当成 bug 查：
 * - 列表接口只给 `participants.total`，**不给整份领取名单**，所以提交数/通过数在
 *   这一层没有 —— 要看它们得进单题看板（那条路另取参与者与提交，见 `TaskInsights.vue`）。
 * - 附件只有**一个数**（`Task.attachmentCount`，列表接口的分组计数）：文件名与下载
 *   走另一条路（`TasksApi.listAttachments` / 下载端点），卡片上那格「附件 N」不等它们。
 * - 「我发布的 / 我领取的」不走这里：那两块有**专为它们准备的接口**
 *   （`/spaces/{id}/me/publishing*`、`/me/participating*`），在 `Mine.vue` 里直接取，
 *   因为那些数（谁在等我审、多少人在我这卡住）全板列表里根本没有。
 * - 首页那句「参与 N 人」是**跨题去重**后的人，拿逐题的 `participants.total` 拼不出来
 *   （那是行数）。它跟着列表那次 `GET /tasks` 一起回来（`queryDistinctParticipants`，
 *   见 `Kpis.participants`），不另开一次请求。
 */
import type { Space, SpaceInviteCode, Task, User } from '@/types'
import type { BoardTask, InviteCode, Manager, Person, ReviewedTask, Role, SpaceInfo, TaskState } from './model'

import { computed, ref } from 'vue'

import { currentInviteCode, splitOrigin } from './model'

import { myHandle } from '@/me'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'

// --- 状态 --------------------------------------------------------------------

const spaceId = ref<number | null>(null)
const spaceRaw = ref<Space | null>(null)
const rawTasks = ref<Task[]>([])
const rawCodes = ref<SpaceInviteCode[]>([])
/** 首页那句「参与 N 人」的数：这一页题目上去重后的参与人数，由 `GET /tasks` 在
 *  **同一个响应里**给（见 `Kpis.participants` 与 `TasksApi.list` 的
 *  `queryDistinctParticipants`）。`null` = 这一次没拿到 —— 拿不到就不显示那一格，
 *  不画一个 0 顶上去。 */
const distinctParticipants = ref<number | null>(null)
const loading = ref(false)
const loadedOnce = ref(false)
/** 空间读不到（不存在、没权限、板子没过审）。外壳拿它换掉整页，而不是留一张空表。 */
const loadFailed = ref(false)

// --- 映射 --------------------------------------------------------------------

function toPerson(user: User | null | undefined): Person {
  if (!user) return { handle: '', name: '（未知）' }
  return { handle: user.username, name: user.nickname || user.username }
}

function toState(task: Task): TaskState {
  if (task.approved === 'DISAPPROVED') return 'REJECTED'
  if (task.approved === 'NONE') return 'PENDING'
  // 过审了但过了截止日 —— 界面上那是「已截止」，不是「已上板」。
  if (task.deadline !== null && task.deadline < Date.now()) return 'CLOSED'
  return 'PUBLISHED'
}

const iso = (ms: number | null | undefined): string => (ms == null ? '' : new Date(ms).toISOString())

export function toBoardTask(task: Task): BoardTask {
  // 出处不是一列，是简介开头的一段文本（见 `model.ts` 的 `splitOrigin`）：认出来的
  // 那一段进 `origin` 变成一枚标记，**同时从 `summary` 里摘掉** —— 不然同一句话在
  // 卡片上会出现两次，一次当标记一次当正文。
  const { summary, origin } = splitOrigin(task.intro)
  return {
    id: String(task.id),
    title: task.name,
    summary,
    origin,
    category: task.category?.name ?? '',
    // 标签就是题目的 topics（真模型里没有单独的一份）：接口按名字给，卡片显示
    // `#名字`，首页的搜索也在这几个名字上匹配（`BoardHome.vue` 的 `visible`）。
    tags: (task.topics ?? []).map((topic) => topic.name),
    publisher: toPerson(task.creator),
    state: toState(task),
    rejectReason: task.rejectReason,
    createdAt: iso(task.createdAt),
    publishedAt: task.publishedAt ? iso(task.publishedAt) : undefined,
    deadline: task.deadline === null ? null : iso(task.deadline),
    // 真库用 0 表示不限，界面用 null。
    participantLimit: task.participantLimit ? task.participantLimit : null,
    minTeamSize: task.minTeamSize ?? 1,
    maxTeamSize: task.maxTeamSize ?? 1,
    videoUrl: task.videoUrl ?? null,
    attachmentCount: task.attachmentCount ?? 0,
    claims: [],
    claimCount: task.participants?.total ?? 0,
    myClaimStatus: task.myClaimStatus ?? null,
  }
}

// --- 加载 --------------------------------------------------------------------

/** 换一个空间。同一个空间重复调用是空操作 —— 路由在两个子页之间切换时会再调一次。 */
export async function loadBoard(id: number, force = false) {
  if (!force && loadedOnce.value && spaceId.value === id) return
  spaceId.value = id
  loading.value = true
  loadFailed.value = false
  try {
    const [spaceRes, taskRes] = await Promise.all([
      SpacesApi.detail(id),
      TasksApi.list({
        space: id,
        pageSize: 100,
        sort_by: 'publishedAt',
        sort_order: 'desc',
        querySpace: false,
        queryJoined: true,
        // 卡片上的标签、以及首页搜索要匹配的那几个词。跟着列表一起回来 ——
        // 一页题再补一次请求，标签就会比卡片晚一步出现。
        queryTopics: true,
        // 首页那句「参与 N 人」跟这一份列表一起回来 —— 它不能是另一次请求，否则
        // 首页多等一轮，而且两次读到的题目集合可能不一样（一边正在审、正在发）。
        queryDistinctParticipants: true,
      }),
    ])
    spaceRaw.value = spaceRes.data.space
    rawTasks.value = taskRes.data.tasks
    distinctParticipants.value = taskRes.data.distinctParticipants ?? null
    loadedOnce.value = true
  } catch {
    // 读不到就是读不到：真接口对「不存在」和「没权限」都答 404（`require_reviewed_space`
    // 就是这么做的），界面上没有区别可讲。**必须在这里接住** —— 上面那个 watch 不会
    // 接 rejected promise，漏出去就是一条未处理的拒绝，而且页面看着像加载失败。
    spaceRaw.value = null
    rawTasks.value = []
    distinctParticipants.value = null
    loadedOnce.value = false
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

/** 拉待审队列。`approved=NONE` 就是待审 —— 与真平台审核页同一条口径。 */
export async function loadPending(): Promise<BoardTask[]> {
  if (spaceId.value === null) return []
  const res = await TasksApi.list({
    space: spaceId.value,
    pageSize: 100,
    sort_by: 'createdAt',
    sort_order: 'asc',
    approved: 'NONE',
    querySpace: false,
    queryJoined: false,
  })
  return res.data.tasks.map(toBoardTask)
}

/** 最近处理过的那几道题 —— 审核页底部那张卡。
 *
 *  「处理过」这件事不在题目状态里（`approved` 说不了是谁、什么时候点的），所以
 *  这里按审核痕迹自己查：通过的一批、驳回的一批，各自按 `reviewedAt` 倒序取
 *  `limit` 条，合起来再倒序取 `limit` 条 —— 两次查询各取 `limit` 足够，因为两边
 *  合起来的前 `limit` 名一定各自落在自己那批的前 `limit` 名里。
 *
 *  这一列是后加的：更早审过的题 `reviewedAt` 是 null，那些行跳过（「最近处理过」
 *  要的是时间，不是「处理过」本身）。 */
export async function loadRecentReviews(limit = 4): Promise<ReviewedTask[]> {
  if (spaceId.value === null) return []
  const [approved, disapproved] = await Promise.all([
    TasksApi.list({
      space: spaceId.value,
      approved: 'APPROVED',
      pageSize: limit,
      sort_by: 'reviewedAt',
      sort_order: 'desc',
      querySpace: false,
      queryJoined: false,
    }),
    TasksApi.list({
      space: spaceId.value,
      approved: 'DISAPPROVED',
      pageSize: limit,
      sort_by: 'reviewedAt',
      sort_order: 'desc',
      querySpace: false,
      queryJoined: false,
    }),
  ])
  const rows: { at: number; row: ReviewedTask }[] = []
  const collected = [
    ...approved.data.tasks.map((task) => ({ task, result: 'APPROVED' as const })),
    ...disapproved.data.tasks.map((task) => ({
      task,
      result: 'DISAPPROVED' as const,
    })),
  ]
  for (const { task, result } of collected) {
    if (task.reviewedAt == null) continue
    rows.push({
      at: task.reviewedAt,
      row: {
        id: String(task.id),
        title: task.name,
        result,
        reviewer: reviewerOf(task.reviewedBy),
        reviewedAt: iso(task.reviewedAt),
      },
    })
  }
  rows.sort((a, b) => b.at - a.at)
  return rows.slice(0, limit).map(({ row }) => row)
}

/** 审核人 id → 名册里的那个人。审题只有本板管理员走得通，所以名册就是答案；
 *  对不到（这个人已经被移出名册）时给 `null`，界面照实说不知道是谁。 */
function reviewerOf(userId: number | null | undefined): Person | null {
  if (typeof userId !== 'number') return null
  const user = spaceRaw.value?.admins.find((a) => a.user.id === userId)?.user
  return user ? toPerson(user) : null
}

/** 邀请码。列表在这里读；改与撤在弹窗里直接走 `SpacesApi.updateInviteCode` /
 *  `revokeInviteCode`（改完那一屏自己重拉，不需要把整块板跟着刷一遍）。 */
export async function loadCodes() {
  if (spaceId.value === null) return
  const res = await SpacesApi.listInviteCodes(spaceId.value)
  rawCodes.value = res.data.inviteCodes ?? []
}

// --- 派生 --------------------------------------------------------------------

/** 「谁能管这块板」的完整名单，**角色一起带出来**：`SpaceInfo` 那两格（`owner` /
 *  `admins`）是给别处算角色用的，各自都没带角色；管理员设置那块弹窗要显示的是
 *  「这个人是什么角色」，所以另起一份，直接映射真接口的 `Space.admins`。
 *
 *  所有者排最前，其余按接口给的顺序（后端 `list_admins` 按 `created_at` 升序，见
 *  `backend/app/domain/space/repositories.py`）。**不拼 `space.owner`** —— 名单里
 *  本来就有它。 */
const ROLE_RANK: Record<'OWNER' | 'ADMIN', number> = { OWNER: 0, ADMIN: 1 }

export const managers = computed<Manager[]>(() => {
  const s = spaceRaw.value
  if (!s) return []
  return s.admins
    .map((a) => ({ person: toPerson(a.user), role: a.role }))
    .sort((a, b) => ROLE_RANK[a.role] - ROLE_RANK[b.role])
})

export const space = computed<SpaceInfo | null>(() => {
  const s = spaceRaw.value
  if (!s) return null
  const owner = s.admins.find((a) => a.role === 'OWNER')
  return {
    id: s.id,
    name: s.name,
    intro: s.intro,
    owner: toPerson(owner?.user),
    admins: s.admins.map((a) => toPerson(a.user)),
    // 服务端不说就是「不是课」：老接口、老数据都不带这个字段，而它们本来就不是课。
    isCourse: s.isCourse === true,
  }
})

export const role = computed<Role>(() => {
  const s = spaceRaw.value
  if (!s) return 'MEMBER'
  const mine = s.admins.find((a) => a.user.username === myHandle())
  return mine ? mine.role : 'MEMBER'
})

/** 「能不能审、能不能看看板」唯一的判据：在不在管理员名单里。 */
export const isManager = computed(() => role.value === 'OWNER' || role.value === 'ADMIN')
/** 只有所有者能改成员角色（真平台如此：`Action.ADMIN` 只挂在 OWNER 上）。 */
export const isOwner = computed(() => role.value === 'OWNER')

export const me = computed<Person>(() => {
  const mine = spaceRaw.value?.admins.find((a) => a.user.username === myHandle())
  if (mine) return toPerson(mine.user)
  return { handle: myHandle(), name: myHandle() }
})

export const failed = computed(() => loadFailed.value)

export const tasks = computed<BoardTask[]>(() => rawTasks.value.map(toBoardTask))
export const boardTasks = computed(() => tasks.value.filter((t) => t.state === 'PUBLISHED'))
export const pendingTasks = computed(() => tasks.value.filter((t) => t.state === 'PENDING'))

export const codes = computed<InviteCode[]>(() =>
  rawCodes.value.map((c) => ({
    id: c.id,
    code: c.code,
    maxUses: c.maxUses ? c.maxUses : null,
    useCount: c.useCount,
    // 毫秒原样带过来（不转 ISO）：`currentInviteCode` 判「还能不能用」吃的就是
    // 这个形状，转一道字符串再转回来只会多一个会写错的地方。
    expiresAt: c.expiresAt ?? null,
    createdAt: iso(c.createdAt),
    note: c.note ?? null,
  }))
)

/**
 * 「当前使用中的码」—— 头部下拉的摘要、成员页「让人进来」、邀请码弹窗三处念同一
 * 条（`currentInviteCode`）。**没有就是 `null`**：不拿一张已经用尽或过期的码顶上，
 * 那会让「当前码」这三个字在界面上变成一句谎话。列表按建码时间排，所以拿到的是
 * 最早那张还开着的。
 */
export const currentCode = computed<InviteCode | null>(() => currentInviteCode(codes.value))

export interface Kpis {
  taskTotal: number
  published: number
  pending: number
  /** 领取**次数**：每题的 `participants.total` 之和。一个人领三道题算三次。 */
  claims: number
  /** 参与**人数**：跨题去重后的人。和 `claims` **同一批题、同一次加载** —— 前者是
   *  次数、后者是人，两个数字摆在同一行上，不能一个数的是这一页、另一个数的是全板。
   *
   *  `null` = 这一次没拿到（服务端没回那一格）。首页那一格随之整块不出现 ——
   *  「参与 0 人」会把「没读到」说成「没人参与」。 */
  participants: number | null
}

export const kpis = computed<Kpis>(() => {
  const list = tasks.value
  return {
    taskTotal: list.length,
    published: list.filter((t) => t.state === 'PUBLISHED').length,
    pending: list.filter((t) => t.state === 'PENDING').length,
    claims: list.reduce((n, t) => n + t.claimCount, 0),
    participants: distinctParticipants.value,
  }
})

/** 这道题我领了没有 —— 靠 `queryJoined` 求出来的 `Task.joined`。 */
export function alreadyClaimed(task: BoardTask): boolean {
  return Boolean(rawTasks.value.find((t) => String(t.id) === task.id)?.joined)
}

// --- 写回 --------------------------------------------------------------------

/** 过审 / 驳回。真接口是 `PATCH /tasks/{id}`，带 `approved` 与 `rejectReason`。 */
export async function reviewTask(id: string, approved: boolean, reason = '') {
  await TasksApi.update(
    Number(id),
    approved ? { approved: 'APPROVED' } : { approved: 'DISAPPROVED', rejectReason: reason }
  )
  await loadBoard(spaceId.value!, true)
}
