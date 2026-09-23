/**
 * 原型的**可写**内存状态。
 *
 * 上一轮反馈原型定下的规矩在这儿继续用：预览里的按钮不该是死的。审一道题、改一次
 * 邀请码人数，列表要真的跟着变 —— 否则「审核完这一步会怎样」只能靠读代码去猜，
 * 而猜错的正是重设计最该被人看见的地方。
 *
 * 这里没有 fetch，也不假装有：原型讲的是一条**新流程**，不是某个接口的返回形状。
 * 要对着真接口验请在本机起 backend。
 */
import { computed, ref } from 'vue'

import {
  type BoardTask,
  type Claimant,
  IDENTITIES,
  INITIAL_CODES,
  type InviteCode,
  PEOPLE,
  type Person,
  type Role,
  SPACE,
  TASKS,
} from './fixtures'

const identityIndex = ref(0)

export const identity = computed(() => IDENTITIES[identityIndex.value])
export const role = computed<Role>(() => identity.value.role)
export const me = computed<Person>(() => identity.value.me)

export function switchIdentity(index: number) {
  identityIndex.value = index
}

/** 当前身份是不是管理员或所有者 —— 「能不能审、能不能看看板」唯一的判据。 */
export const isManager = computed(() => role.value === 'OWNER' || role.value === 'ADMIN')
/** 只有所有者能改成员角色（真平台如此，见 backend/app/auth/domains/space.py 的
 *  `SPACE_PERMISSIONS`：`Action.ADMIN` 只挂在 OWNER 上）。 */
export const isOwner = computed(() => role.value === 'OWNER')

export const tasks = ref<BoardTask[]>(TASKS.map((t) => ({ ...t, claims: [...t.claims] })))
export const codes = ref<InviteCode[]>(INITIAL_CODES.map((c) => ({ ...c })))
export const space = SPACE

/** 审核通过。**自己发的题自己也能过** —— 这是重设计要立的那条规则，所以它在这里是
 *  一段无条件通过的代码，而不是一句注释。 */
export function approveTask(id: string) {
  const task = tasks.value.find((t) => t.id === id)
  if (!task) return
  task.state = 'PUBLISHED'
  task.publishedAt = new Date().toISOString()
  task.rejectReason = undefined
  task.reviewedBy = me.value
}

export function rejectTask(id: string, reason: string) {
  const task = tasks.value.find((t) => t.id === id)
  if (!task) return
  task.state = 'REJECTED'
  task.rejectReason = reason
  task.reviewedBy = me.value
}

/** 出一道新题。默认就是「待审核」—— 发题的人是不是管理员都一样，只是管理员发的
 *  题在审核队列里会多一个「你自己就能过」的提示。 */
export function publishTask(draft: {
  title: string
  summary: string
  category: string
  tags: string[]
  participantLimit: number | null
  minTeamSize: number
  maxTeamSize: number
  deadlineDays: number
  videoUrl?: string | null
  /** 从 PDF 批量发时写「PDF · 第 2 页」，手写的题没有这一项。 */
  origin?: string
}): BoardTask {
  const task: BoardTask = {
    id: `new-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
    title: draft.title,
    summary: draft.summary,
    category: draft.category,
    tags: draft.tags,
    publisher: me.value,
    state: 'PENDING',
    createdAt: new Date().toISOString(),
    deadline: new Date(Date.now() + draft.deadlineDays * 86_400_000).toISOString(),
    participantLimit: draft.participantLimit,
    minTeamSize: draft.minTeamSize,
    maxTeamSize: draft.maxTeamSize,
    videoUrl: draft.videoUrl ?? null,
    origin: draft.origin,
    submitted: 0,
    passed: 0,
    claimTrend: [],
    claims: [],
  }
  tasks.value = [task, ...tasks.value]
  return task
}

/** 从 PDF 生成的那批草稿，勾选确认之后落到这里 —— 和手写一样进待审队列。
 *  真平台是 `POST /tasks/publish/from-pdf/confirm`：预览归预览，不确认不落库。 */
export function publishFromPdf(
  drafts: { title: string; summary: string; category: string; sourcePage: number }[]
): BoardTask[] {
  return drafts.map((d) =>
    publishTask({
      title: d.title,
      summary: d.summary,
      category: d.category,
      tags: ['来自 PDF'],
      participantLimit: null,
      minTeamSize: 1,
      maxTeamSize: 1,
      deadlineDays: 14,
      origin: `PDF · 第 ${d.sourcePage} 页`,
    })
  )
}

/** 领取一道题。上限满了就什么都不做，返回 false —— 界面要把「满了」这件事说出来。 */
export function claimTask(id: string): boolean {
  const task = tasks.value.find((t) => t.id === id)
  if (!task) return false
  if (alreadyClaimed(task)) return false
  if (task.participantLimit !== null && task.claims.length >= task.participantLimit) return false
  const entry: Claimant = {
    handle: me.value.handle,
    name: me.value.name,
    at: new Date().toISOString(),
    status: 'IN_PROGRESS',
  }
  task.claims = [...task.claims, entry]
  return true
}

export function alreadyClaimed(task: BoardTask): boolean {
  return task.claims.some((c) => c.handle === me.value.handle)
}

export function myClaim(task: BoardTask): Claimant | undefined {
  return task.claims.find((c) => c.handle === me.value.handle)
}

/** 名单一动，提交数与通过数就得跟着重算 —— 假数据里这两个数是 `finalize()` 从名单
 *  派生出来的，派生关系不能在「界面能改名单」之后断掉。 */
function recompute(task: BoardTask) {
  task.submitted = task.claims.filter((c) => c.status !== 'IN_PROGRESS').length
  task.passed = task.claims.filter((c) => c.status === 'PASSED').length
}

/** 交作业：把我的领取记录从「进行中」推到「已提交」。真平台是
 *  `POST /tasks/{id}/participants/{pid}/submissions`，交什么由题目的 submissionSchema 定。 */
export function submitWork(id: string): boolean {
  const task = tasks.value.find((t) => t.id === id)
  if (!task) return false
  const mine = myClaim(task)
  if (!mine || mine.status !== 'IN_PROGRESS') return false
  mine.status = 'SUBMITTED'
  recompute(task)
  return true
}

/** 判作业：**出题人本人或这块板的管理员**都能判（真平台 `may_teach_task`）——
 *  这与「题目上板审核只有管理员能做」不是同一条判据。真接口是
 *  `POST .../submissions/{sid}/review`，带 accepted / score / comment。 */
export function reviewWork(id: string, handle: string, accepted: boolean): boolean {
  const task = tasks.value.find((t) => t.id === id)
  if (!task || !canManageTask(task)) return false
  const claim = task.claims.find((c) => c.handle === handle)
  if (!claim || claim.status !== 'SUBMITTED') return false
  claim.status = accepted ? 'PASSED' : 'REJECTED'
  recompute(task)
  return true
}

// --- 邀请码 ------------------------------------------------------------------

export function addCode(spec: { maxUses: number | null; expiresAt: string | null; note: string }) {
  const raw = Math.random().toString(36).slice(2, 6).toUpperCase()
  codes.value = [
    {
      code: `BOARD-${raw}`,
      maxUses: spec.maxUses,
      useCount: 0,
      expiresAt: spec.expiresAt,
      createdBy: me.value,
      createdAt: new Date().toISOString(),
      note: spec.note,
      revoked: false,
    },
    ...codes.value,
  ]
}

export function updateCode(code: string, spec: { maxUses: number | null; expiresAt: string | null }) {
  const row = codes.value.find((c) => c.code === code)
  if (!row) return
  row.maxUses = spec.maxUses
  row.expiresAt = spec.expiresAt
}

export function revokeCode(code: string) {
  const row = codes.value.find((c) => c.code === code)
  if (row) row.revoked = true
}

// --- 派生视图（各页面共用的那几个口径）----------------------------------------

export const pendingTasks = computed(() => tasks.value.filter((t) => t.state === 'PENDING'))

export const boardTasks = computed(() => tasks.value.filter((t) => t.state === 'PUBLISHED'))

export const myPublished = computed(() => tasks.value.filter((t) => t.publisher.handle === me.value.handle))

export const myClaimed = computed(() => tasks.value.filter((t) => alreadyClaimed(t)))

/** 我这个身份能不能动这道题：管理员能管全部，普通用户只能管自己发的。 */
export function canManageTask(task: BoardTask): boolean {
  return isManager.value || task.publisher.handle === me.value.handle
}

export interface Kpis {
  taskTotal: number
  published: number
  pending: number
  claims: number
  submissions: number
  passed: number
  completionRate: number
  participants: number
}

export const kpis = computed<Kpis>(() => {
  const list = tasks.value
  const published = list.filter((t) => t.state === 'PUBLISHED').length
  const pending = list.filter((t) => t.state === 'PENDING').length
  const claims = list.reduce((n, t) => n + t.claims.length, 0)
  const submissions = list.reduce((n, t) => n + t.submitted, 0)
  const passed = list.reduce((n, t) => n + t.passed, 0)
  const participants = new Set(list.flatMap((t) => t.claims.map((c) => c.handle))).size
  return {
    taskTotal: list.length,
    published,
    pending,
    claims,
    submissions,
    passed,
    completionRate: submissions ? Math.round((passed / submissions) * 100) : 0,
    participants,
  }
})

/** 每道题的领取数排行，看板和「热门题」都要。 */
export const claimRanking = computed(() =>
  [...tasks.value]
    .filter((t) => t.state !== 'PENDING')
    .sort((a, b) => b.claims.length - a.claims.length)
    .slice(0, 6)
)

export const publisherRanking = computed(() => {
  const byHandle = new Map<
    string,
    { person: Person; tasks: number; claims: number; passed: number; submitted: number }
  >()
  for (const t of tasks.value) {
    const row = byHandle.get(t.publisher.handle) ?? {
      person: t.publisher,
      tasks: 0,
      claims: 0,
      passed: 0,
      submitted: 0,
    }
    row.tasks += 1
    row.claims += t.claims.length
    row.passed += t.passed
    row.submitted += t.submitted
    byHandle.set(t.publisher.handle, row)
  }
  return [...byHandle.values()].sort((a, b) => b.claims - a.claims).slice(0, 6)
})

export const CATEGORY_SPLIT = computed(() => {
  const counts = new Map<string, number>()
  for (const t of tasks.value) counts.set(t.category, (counts.get(t.category) ?? 0) + 1)
  return [...counts.entries()].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count)
})

export const statusSplit = computed(() => {
  const published = tasks.value.filter((t) => t.state === 'PUBLISHED').length
  const pending = tasks.value.filter((t) => t.state === 'PENDING').length
  const rejected = tasks.value.filter((t) => t.state === 'REJECTED').length
  return [
    { label: '已上板', count: published, tone: 'ok' as const },
    { label: '待审核', count: pending, tone: 'warn' as const },
    { label: '已驳回', count: rejected, tone: 'danger' as const },
  ]
})

export const PEOPLE_INDEX = PEOPLE
