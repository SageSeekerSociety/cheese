// 外站题（今天只有 YOJ）：一道题挂在那边的一个题号，加上每个领取者过没过。
//
// 和飞书那份（`feishu.ts`）同一个理由拆出来：两边共用一个域，任务侧走
// `/tasks/{taskId}/external-problem`，管理端走 `/admin/integrations/external-problem`。
//
// 两处和别处不一样，写在这里免得调用方自己猜：
//
// 1. **题号不是任务的一个字段。** 它是自己的一份资源（`PUT` / `DELETE`），不进
//    `PATCH /tasks/{taskId}` 那个正文。所以发题、改题那边保存完题目要单独写一次。
// 2. **同步是慢的，失败也是 200。** 一次同步要真的去对面翻页（后端做在这次请求里），
//    对面不通时回的那一份上 `status: 'failed'` 带着原因。上一次成功的结论留着，
//    所以界面上「这是几点的数」和「这次同没同成」是两件事。
import { request } from '../api'

/** 一个领取者在那道题上的结论。`participantId` 就是名单里那一行的 `id`。 */
export interface ExternalProblemResult {
  participantId: number
  /** 学号（拿去外站核对用的那个）。 */
  studentNo: string
  solved: boolean
  acceptedAt: string | null
}

/** 一道题的外站那一块。没挂题号时 `problemNo` 是空串、`results` 是空的 —— 那是答案，不是错误。 */
export interface TaskExternalProblem {
  problemNo: string
  /** 哪个外站；今天恒为 `yoj`。 */
  source: string
  lastSyncedAt: string | null
  /** `''` 还没同步过 / `ok` / `failed`。 */
  status: '' | 'ok' | 'failed'
  /** 失败原因的原文（`no_account` 这种），排查用，不给人看 —— 界面上按 `status` 自己说。 */
  error: string
  results: ExternalProblemResult[]
}

/** 这道题挂在哪个外站题号上，以及每个人过没过。没挂过题号时也回 200。 */
export function getTaskExternalProblem(taskId: number): Promise<TaskExternalProblem> {
  return request<TaskExternalProblem>(`/tasks/${taskId}/external-problem`)
}

/**
 * 挂上、或者换掉题号。
 *
 * 换成一个**另一个**题号会把上一次的结果删掉（那些数说的是另一道题）；同一个题号再
 * 挂一次是幂等的。所以调用方要先比一下值，没变就别发这一次。
 */
export function setTaskExternalProblem(taskId: number, problemNo: string): Promise<TaskExternalProblem> {
  return request<TaskExternalProblem>(`/tasks/${taskId}/external-problem`, {
    method: 'PUT',
    body: JSON.stringify({ problemNo }),
  })
}

/** 撤掉挂载：题号和结果一起没。清空题号那一格时走这条。 */
export function clearTaskExternalProblem(taskId: number): Promise<void> {
  return request<void>(`/tasks/${taskId}/external-problem`, { method: 'DELETE' })
}

/**
 * 现在去外站看一眼。**慢**：这一趟在服务端做完才回，对面不通就是一次超时。
 * 失败也是 200，看回执上的 `status`。
 */
export function syncTaskExternalProblem(taskId: number): Promise<TaskExternalProblem> {
  return request<TaskExternalProblem>(`/tasks/${taskId}/external-problem/sync`, { method: 'POST' })
}

/* ---- 管理端（`/admin/integrations`）----
 *
 * 拿外站眼里的一个账号（对 YOJ 来说是教师工号），用来读这道题上所有人的成绩。
 * 密码只写不回显：读回来的结构里根本没有它，所以 `password` 的空串表示「不改已经
 * 存下的那一个」，和飞书应用那一页同一条规矩。
 */

export interface ExternalProblemAccount {
  configured: boolean
  source: string
  username: string
  updated_by: string
  updated_at: string | null
}

export function getExternalProblemAccount(): Promise<ExternalProblemAccount> {
  return request<ExternalProblemAccount>('/admin/integrations/external-problem')
}

export function saveExternalProblemAccount(body: {
  username: string
  password: string
}): Promise<ExternalProblemAccount> {
  return request<ExternalProblemAccount>('/admin/integrations/external-problem', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
