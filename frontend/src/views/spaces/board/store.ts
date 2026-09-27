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
 * - 题目**还没有附件这一层**（真表没有该字段），`files` 一律是空数组。
 * - 「我发布的 / 我领取的」不走这里：那两块有**专为它们准备的接口**
 *   （`/spaces/{id}/me/publishing*`、`/me/participating*`），在 `Mine.vue` 里直接取，
 *   因为那些数（谁在等我审、多少人在我这卡住）全板列表里根本没有。
 */
import type { Space, SpaceInviteCode, Task, User } from '@/types'
import type { BoardTask, InviteCode, Person, Role, SpaceInfo, TaskState } from './model'

import { computed, ref } from 'vue'

import { myHandle } from '@/me'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'

// --- 状态 --------------------------------------------------------------------

const spaceId = ref<number | null>(null)
const spaceRaw = ref<Space | null>(null)
const rawTasks = ref<Task[]>([])
const rawCodes = ref<SpaceInviteCode[]>([])
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
  return {
    id: String(task.id),
    title: task.name,
    summary: task.intro,
    category: task.category?.name ?? '',
    tags: [],
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
    files: [],
    claims: [],
    claimCount: task.participants?.total ?? 0,
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
      }),
    ])
    spaceRaw.value = spaceRes.data.space
    rawTasks.value = taskRes.data.tasks
    loadedOnce.value = true
  } catch {
    // 读不到就是读不到：真接口对「不存在」和「没权限」都答 404（`require_reviewed_space`
    // 就是这么做的），界面上没有区别可讲。**必须在这里接住** —— 上面那个 watch 不会
    // 接 rejected promise，漏出去就是一条未处理的拒绝，而且页面看着像加载失败。
    spaceRaw.value = null
    rawTasks.value = []
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

/** 邀请码。**只有列和建** —— 改码的接口真平台还没有（那是另一批）。 */
export async function loadCodes() {
  if (spaceId.value === null) return
  const res = await SpacesApi.listInviteCodes(spaceId.value)
  rawCodes.value = res.data.inviteCodes ?? []
}

// --- 派生 --------------------------------------------------------------------

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
    expiresAt: c.expiresAt == null ? null : iso(c.expiresAt),
    createdAt: iso(c.createdAt),
  }))
)

export interface Kpis {
  taskTotal: number
  published: number
  pending: number
  claims: number
}

export const kpis = computed<Kpis>(() => {
  const list = tasks.value
  return {
    taskTotal: list.length,
    published: list.filter((t) => t.state === 'PUBLISHED').length,
    pending: list.filter((t) => t.state === 'PENDING').length,
    claims: list.reduce((n, t) => n + t.claimCount, 0),
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
