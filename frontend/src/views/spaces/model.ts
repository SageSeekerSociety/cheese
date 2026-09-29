/**
 * 空间页面共用的纯函数：邀请码是否可用、题目简介里的出处、公告的显示顺序。
 */
import type { SpaceAnnouncement } from '@/types'

/** 一张邀请码现在的状态。库里只有次数与期限，两者都能让一张码失效。 */
export interface InviteCodeStatus {
  key: 'usable' | 'expired' | 'exhausted'
  label: string
  color: string
}

/**
 * 一张码还算不算数 —— **「当前使用中的码」唯一的判据**。
 *
 * 收的是真接口的形状：`maxUses` 为 0 或 `null` 都是不限。
 */
export function inviteCodeStatus(
  code: { maxUses: number | null; useCount: number; expiresAt: number | null },
  now: number = Date.now()
): InviteCodeStatus {
  if (code.expiresAt !== null && code.expiresAt <= now) {
    return { key: 'expired', label: '已过期', color: 'warning' }
  }
  if (code.maxUses !== null && code.maxUses > 0 && code.useCount >= code.maxUses) {
    return { key: 'exhausted', label: '已用尽', color: 'error' }
  }
  return { key: 'usable', label: '可用', color: 'success' }
}

/** 还能用的第一张；没有就是 `null`（不拿一张废码顶上）。列表按建码时间排，所以
 *  这是**最早那张还开着的**。
 *
 *  `now` 与 `inviteCodeStatus` 同一个意思，只为测试能钉住「过期的被跳过」这件事。 */
export function currentInviteCode<T extends { maxUses: number | null; useCount: number; expiresAt: number | null }>(
  codes: readonly T[],
  now: number = Date.now()
): T | null {
  return codes.find((c) => inviteCodeStatus(c, now).key === 'usable') ?? null
}

// --- 出处 --------------------------------------------------------------------

/**
 * 出处前缀。真题目模型里**没有**「来源」这一列：从 PDF 生成的那批题，出处是写进**简介开头**的一段字 ——
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

// --- 公告 --------------------------------------------------------------------

/** 公告的显示顺序：**置顶排最前，其余按发布时间倒序**。
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
