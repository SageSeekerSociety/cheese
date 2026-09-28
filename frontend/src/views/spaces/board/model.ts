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
import type { SpaceAnnouncement } from '@/types'

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
  /** 这道题是怎么来的，例如「PDF · 第 2 页」。手写的题没有这一项。
   *
   *  它不是接口来的：后端没有出处这一列，从 PDF 发出去的那批把出处写进了简介开头，
   *  `store.ts` 映射时用 `splitOrigin` 从 `intro` 里认出来，**正文里那串字同时被摘掉**。
   *  所以这一格和 `summary` 是同一段文本的两半，不能各填各的。 */
  origin?: string
  /** 发题时附上的材料。真平台还没有这一层，暂时是空数组。 */
  files: TaskFile[]
  claims: Claimant[]
  /** 领取人数（真接口给的是 `participants.total`，不是整份名单）。 */
  claimCount: number
}

export interface InviteCode {
  /** 真库主键。改码与撤销都按它认人，所以它必须一路带到这里。 */
  id: number
  code: string
  /** 可用人数上限；`null` = 不限（真库用 0 表示不限）。 */
  maxUses: number | null
  useCount: number
  /** 有效期终点；`null` = 永不过期。 */
  expiresAt: string | null
  createdAt: string
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

// --- 出处 --------------------------------------------------------------------

/**
 * 出处前缀。真题目模型里**没有**「来源」这一列，也不给它加（见 `BoardTask.origin`
 * 那一格的注释）：从 PDF 生成的那批题，出处是写进**简介开头**的一段字 ——
 * `GET /tasks` 回来的就是一段带前缀的 `intro`，没有任何结构化字段。所以「这道题从
 * 哪来」在真数据里不是读某一格，而是**认出正文开头那一小段**，摘掉它、单独给人看。
 *
 * 写这一段的是从 PDF 发题那条路（第八批 #1793）：`【PDF · 第 N 页】` **紧跟题干、
 * 中间不换行**。所以这里也**不能要求那串之后有换行** —— 要求了，真从 PDF 发出来的
 * 题一个都认不出来。页号是 1 起的整数（`draftPage(index) = index + 1`）。
 */
const ORIGIN_PREFIX = /^【PDF · 第 \d+ 页】/

/**
 * 把一段简介拆成「正文」与「出处」。
 *
 * 认不出来时只回正文 —— 手写的题走的都是这一支。
 *
 * **误判的边界，认了**：判据只有「开头是不是那一串」。手写的题如果简介恰好以
 * `【PDF · 第 3 页】` 开头，就会被当成 PDF 来的：那串字从正文里消失、变成一枚标。
 * 要消掉它就得有一个「这道题是 PDF 发的」的痕迹，而真库里没有（上面那段说的就是
 * 这件事）—— 拿别的信号去猜只会猜错得更离谱。代价写在这里，不埋在代码里。
 */
export function splitOrigin(intro: string): { summary: string; origin?: string } {
  const prefix = ORIGIN_PREFIX.exec(intro)?.[0]
  if (!prefix) return { summary: intro }
  return {
    // 那对书名号是给机器认的，给人看的是里面那段（原型上也是「PDF · 第 2 页」）。
    origin: prefix.slice(1, -1),
    // 摘干净：前缀后面紧跟的就是题干，不留一个空格在开头。
    summary: intro.slice(prefix.length).trimStart(),
  }
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

// --- 公告 --------------------------------------------------------------------

/** 公告的显示顺序：**置顶排最前，其余按发布时间倒序**。公告列表与首页那条横幅共用
 *  这一个判据 —— 两处各排一次，迟早会出现「横幅上是这条、列表第一条是另一条」。
 *
 *  `pinned` 缺省当 `false`：加这一格之前发出去的公告都没有它。时间也带一层兜底 ——
 *  公告是 jsonb 里的一段，元素形状没有 schema 兜着。
 *
 *  这是**显示**口径，不是数据口径：`stores/space.ts` 的 `updateAnnouncement(index, …)`
 *  是按下标写回的，把 store 里那份数组本身排序，改动会写到别的条目上。要排就排副本
 *  （`sortAnnouncements`），不然就把原下标一起带在手上。 */
export function compareAnnouncements(a: SpaceAnnouncement, b: SpaceAnnouncement): number {
  if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1
  return (b.createdAt ?? 0) - (a.createdAt ?? 0)
}

/** 排好序的副本，store 里那份的顺序一个字节都不动。 */
export function sortAnnouncements(list: SpaceAnnouncement[]): SpaceAnnouncement[] {
  return [...list].sort(compareAnnouncements)
}
