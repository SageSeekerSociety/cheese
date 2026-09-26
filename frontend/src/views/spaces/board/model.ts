/**
 * 空间新界面的视图模型与纯函数。
 *
 * 这一份是从重设计原型（`src/proto-board/fixtures.ts`）里**切出来的**：原型那份把
 * 「讲设计用的假数据」和「界面上真正在用的类型与格式化」混在一个文件里，落到真界面
 * 时只有后半截能带走。所以这里只有**类型 + 纯函数**，一行假数据都没有 —— 数据由
 * `store.ts` 从真接口取。
 *
 * 字段名照真模型取：`Task.approved` / `participant_limit` / `min_team_size` /
 * `deadline`、`SpaceInviteCode.max_uses` / `use_count` / `expires_at`。这里的
 * `BoardTask` 只是**把真 `Task` 摊平成界面好用的形状**，见 `store.ts` 的 `toBoardTask`。
 */

/** 空间内的角色。与后端 `SpaceAdminRole`（OWNER=0 / ADMIN=1）对应，多出来的 MEMBER
 *  是「不在管理员名单里」这件事的显式名字。 */
export type Role = 'OWNER' | 'ADMIN' | 'MEMBER'

/** 题目状态。真库只用 `Task.approved`（APPROVED / DISAPPROVED / NONE）一个字段表
 *  三态，这里拆成名字，因为界面上要显示的不是那个字符串。 */
export type TaskState = 'PENDING' | 'PUBLISHED' | 'REJECTED' | 'CLOSED'

export interface Person {
  handle: string
  name: string
}

export interface Claimant {
  handle: string
  name: string
  at: string
  team?: string
  status: 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'
}

/** 题目的附件。真平台上题目还没有这一层（题目表没有附件字段），这一栏先留着空，
 *  等附件那一批落地再填 —— 界面按「有就列、没有就不显示」写，不假装它已经存在。 */
export interface TaskFile {
  name: string
  /** 字节。 */
  size: number
  kind: 'pdf' | 'image' | 'code' | 'archive' | 'doc'
  downloads: number
}

export interface BoardTask {
  id: string
  title: string
  summary: string
  category: string
  tags: string[]
  publisher: Person
  state: TaskState
  rejectReason?: string
  createdAt: string
  publishedAt?: string
  /** ISO。真库是毫秒时间戳，`store.ts` 转换。`null` 表示不设截止。 */
  deadline: string | null
  /** 领取人数上限。`null` = 不限（真库用 0 表示不限）。 */
  participantLimit: number | null
  /** 小队规模限制。`1/1` 就是「只能单人领」。 */
  minTeamSize: number
  maxTeamSize: number
  /** 讲解视频。真库 `Task.video_url`；详情页只把 B 站链接转成内嵌播放器。 */
  videoUrl?: string | null
  /** 这道题是怎么来的。从 PDF 生成的那批会写「PDF · 第 2 页」，手写的没有这一项。 */
  origin?: string
  /** 发题时附上的材料。真平台还没有这一层，暂时是空数组。 */
  files: TaskFile[]
  claims: Claimant[]
  /** 领取人数（真接口给的是 `participants.total`，不是整份名单）。 */
  claimCount: number
}

export interface InviteCode {
  code: string
  /** 可用人数上限；`null` = 不限（真库用 0 表示不限）。 */
  maxUses: number | null
  useCount: number
  /** 有效期终点；`null` = 永不过期。 */
  expiresAt: string | null
  createdAt: string
  revoked: boolean
}

export interface SpaceInfo {
  id: number
  name: string
  intro: string
  owner: Person
  admins: Person[]
  /**
   * 这块板是不是一门课（服务端算的，见 `types/spaces.ts` 的 `isCourse`）。
   * 题目板外壳只拿它决定**露不露那格「课程」** —— 课那几屏（教学单元、作业、小测、
   * 小组）在老树的 `SpacesCourse*` 上，没有这一格，课里的人从题目板就走不回去。
   */
  isCourse: boolean
}

export const ROLE_LABEL: Record<Role, string> = {
  OWNER: '所有者',
  ADMIN: '管理员',
  MEMBER: '成员',
}

export const STATE_LABEL: Record<TaskState, string> = {
  PENDING: '待审核',
  PUBLISHED: '已上板',
  REJECTED: '已驳回',
  CLOSED: '已截止',
}

export const CLAIM_LABEL: Record<Claimant['status'], string> = {
  IN_PROGRESS: '进行中',
  SUBMITTED: '已提交',
  PASSED: '已通过',
  REJECTED: '未通过',
}

/** 板上现在真正可领的题：审过了，且没到截止日。 */
export function isOpen(task: BoardTask): boolean {
  return task.state === 'PUBLISHED' && (!task.deadline || new Date(task.deadline).getTime() > Date.now())
}

export function deadlineText(task: BoardTask): string {
  if (!task.deadline) return '不限截止'
  const days = Math.ceil((new Date(task.deadline).getTime() - Date.now()) / 86_400_000)
  if (days < 0) return `已截止 ${-days} 天`
  if (days === 0) return '今天截止'
  return `${days} 天后截止`
}
