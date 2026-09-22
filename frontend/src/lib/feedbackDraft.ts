/**
 * 反馈提交表单的落盘（localStorage）。
 *
 * 和 `composerDrafts.ts`（聊天输入框）是同一件事的第二个例子，理由也一样：**每次
 * 发版，开着的页面都会自己刷新**（service worker 换新 → `window.location.reload()`，
 * 见 pwa.ts）。刷新没有「关掉抽屉」这一步，`onBeforeUnmount` 一个都不会跑，于是
 * 写了一半的反馈在用户眼皮底下消失 —— 而反馈表单比聊天输入框长得多（标题、正文、
 * 复现步骤、期望），丢一次就是几百字。
 *
 * 与 composerDrafts 不同的一点：那边一个话题一条记录（话题 id 进键名），这边**只有
 * 一份**。反馈不挂在话题上，「我正在写的是哪一条」这件事本来就没有第二个来源，编一个
 * 出来只会多一个能和屏幕对不上的维度。
 *
 * **两个槽位**，因为是两条路会用同一张表单：人自己点「提交反馈」，和会话里那张提案卡
 * 按「提交反馈」（带着 agent 的现场）。后者整份替换表单内容，不然「发送」会把上一张
 * 卡的东西发出去。所以替换之前先把**人已经打的那份挪去 parked**（见 `parkFeedbackDraft`），
 * 它下次打开表单时自己回来 —— 一次误操作不该让人丢掉几百字。
 *
 * 记录里**没有凭据**：可见范围、类型这些都是本机偏好，正文里可能出现的密钥是用户
 * 自己写进去的，和它存在哪里无关（`localStorage` 不出这台浏览器）。
 */

import type { FeedbackKind, FeedbackVisibility } from '@/cx_types'
import type { FeedbackDraft } from '@/stores/feedback'

const KEY_PREFIX = 'cheese.feedback-draft.v1:'

/** 只有一份，所以键是常量。前缀带版本：改形状时换 v2，老记录自然读不出来。 */
const KEY = `${KEY_PREFIX}current`

/** 攒了太久没发出去的草稿不值得再弹回来：一周，和聊天草稿同一个口径。 */
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

/** 单条记录的上限（字符数）。localStorage 只有几 MB 且写满会抛异常；超了就不存，
 *  界面上一个字都不会少。 */
const MAX_CHARS = 200_000

interface StoredDraft {
  savedAt: number
  /** 「正在写的」那一份。为空 = 这一格没有东西（提交掉了、或者从没有过）—— 它和
   *  parked 是**两格独立的状态**，用一个空对象表示「没有」会让 `loadFeedbackDraft`
   *  返回一份「空草稿」，调用方就分不出「捞回来了一份空的」和「什么都没捞到」。 */
  draft: FeedbackDraft | null
  /** 被整份替换时挪到这里的那一份，见文件头。 */
  parked: FeedbackDraft | null
}

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    // 站点数据被禁用时连读这个属性都会抛。
    return null
  }
}

/**
 * 「这份草稿里有人的字吗」。空表单等于「这里没有东西」—— 拿它去覆盖记录，或者拿它
 * 当成「有东西，别覆盖」，都是错的。
 */
export function isDraftMeaningful(draft: FeedbackDraft | null | undefined): boolean {
  if (!draft) return false
  return !!(
    draft.title.trim() ||
    draft.body.trim() ||
    draft.repro?.trim() ||
    draft.expectation?.trim() ||
    draft.attachments.length ||
    draft.proposal
  )
}

/** 词表的兜底枚举。**不从 `KIND_LABEL` 之类的展示表里推导**：那张表哪天少一个键，
 *  这里就会把那一类反馈判成「读不懂」，而用户看到的是自己的草稿凭空消失 —— 一个
 *  改标签颜色的动作不该有这种后果。 */
const KINDS: readonly FeedbackKind[] = ['bug', 'suggestion', 'other']
const VISIBILITIES: readonly FeedbackVisibility[] = ['public', 'private']

const str = (value: unknown): string => (typeof value === 'string' ? value : '')

/**
 * 只信我们自己写下去的、且还认得出来的那些字段 —— **逐字段点名，不整个 spread 出来**。
 * 这份对象最终会走到 `POST /feedback` 的请求体里（见 `toCreateBody`），而它现在还
 * 经过了 `localStorage` 这一道人手可改的关口：多带一个没点名的字段，就是让本机上的
 * 一个字符串直接变成请求里的一个字段。
 */
function parseDraft(value: unknown): FeedbackDraft | null {
  if (typeof value !== 'object' || value === null) return null
  const raw = value as Record<string, unknown>
  if (typeof raw.title !== 'string' || typeof raw.body !== 'string') return null
  const kind = KINDS.find((k) => k === raw.kind)
  const visibility = VISIBILITIES.find((v) => v === raw.visibility)
  if (!kind || !visibility) return null
  const draft: FeedbackDraft = {
    kind,
    visibility,
    title: raw.title,
    body: raw.body,
    attachments: Array.isArray(raw.attachments) ? raw.attachments.filter((n) => typeof n === 'string') : [],
    attachContext: !!raw.attachContext,
    repro: str(raw.repro),
    expectation: str(raw.expectation),
  }
  // 提案卡那一路的两块：形状不对就当没有。它们是**指针**（去哪张卡、带上哪段现场），
  // 指向一个半截的对象时，「发送」要么打不开、要么把空现场当成现场发出去。
  const agent = raw.fromAgent as Record<string, unknown> | undefined
  if (agent && typeof agent === 'object') {
    draft.fromAgent = {
      whatHappened: str(agent.whatHappened),
      repro: str(agent.repro),
      evidence: str(agent.evidence),
      ...(typeof agent.sessionId === 'string' ? { sessionId: agent.sessionId } : {}),
      ...(typeof agent.environment === 'string' ? { environment: agent.environment } : {}),
    }
  }
  const proposal = raw.proposal as Record<string, unknown> | undefined
  if (proposal && typeof proposal.topicId === 'string' && typeof proposal.blockId === 'string') {
    draft.proposal = { topicId: proposal.topicId, blockId: proposal.blockId }
  }
  return draft
}

function read(now: number): StoredDraft | null {
  const store = storage()
  if (!store) return null
  try {
    const rawText = store.getItem(KEY)
    if (!rawText) return null
    const parsed = JSON.parse(rawText) as Partial<StoredDraft>
    if (typeof parsed?.savedAt !== 'number' || now - parsed.savedAt > MAX_AGE_MS) {
      forgetFeedbackDraft()
      return null
    }
    return {
      savedAt: parsed.savedAt,
      draft: parseDraft(parsed.draft),
      parked: parseDraft(parsed.parked),
    }
  } catch {
    // 读不懂就清掉：留一条坏记录只会让每次打开都白读一遍。
    forgetFeedbackDraft()
    return null
  }
}

function write(record: StoredDraft): void {
  const store = storage()
  if (!store) return
  try {
    const serialized = JSON.stringify(record)
    if (serialized.length > MAX_CHARS) return
    store.setItem(KEY, serialized)
  } catch {
    // 写满了、被禁用了、或者草稿本身序列化不了 —— 都不该让表单出问题。
  }
}

/** 存一份「正在写的」。空草稿等于「这里没有东西」，直接删记录 —— 不能让一条空记录
 *  把上次的顶掉。**parked 那一份原样保留**：写这一份和那一份是两件事。 */
export function saveFeedbackDraft(draft: FeedbackDraft, now: number = Date.now()): void {
  if (!isDraftMeaningful(draft)) return
  const existing = read(now)
  write({ savedAt: now, draft, parked: existing?.parked ?? null })
}

/** 取回那份「打了一半」的。空草稿、过期、读不懂都回 null。 */
export function loadFeedbackDraft(now: number = Date.now()): FeedbackDraft | null {
  return read(now)?.draft ?? null
}

/** 忘掉「正在写的」那一份，**留着 parked**。提交成功走这条：提交掉的是表单上这一份，
 *  而被替换下去的那一份（见 `parkFeedbackDraft`）还是人自己打了一半的，不该跟着没了。 */
export function clearLiveFeedbackDraft(now: number = Date.now()): void {
  const existing = read(now)
  if (!existing) return
  if (!existing.parked) {
    forgetFeedbackDraft()
    return
  }
  write({ savedAt: now, draft: null, parked: existing.parked })
}

/**
 * 把现在这份挪去 parked —— 调用方马上要用别的内容整份替换表单（提案卡那条路）。
 * **不挪的话人家的字就没了**，而这次替换又不是他的本意。
 *
 * 「正在写的」那一格同时清掉：调用方紧接着就会写进它要替换成的那一份。
 */
export function parkFeedbackDraft(draft: FeedbackDraft, now: number = Date.now()): void {
  if (!isDraftMeaningful(draft)) return
  write({ savedAt: now, draft: null, parked: draft })
}

/** 取出 parked 那份并清空那一格（它回到表单上了，不再需要另存一份）。 */
export function takeParkedFeedbackDraft(now: number = Date.now()): FeedbackDraft | null {
  const existing = read(now)
  if (!existing?.parked) return null
  const parked = existing.parked
  write({ savedAt: now, draft: existing.draft, parked: null })
  return parked
}

/** 抹掉这一格记录（两份都算）。换人登录 / 退出登录、以及两条都空下来时用。 */
export function forgetFeedbackDraft(): void {
  const store = storage()
  if (!store) return
  try {
    store.removeItem(KEY)
  } catch {
    // 存储不可用时静默跳过。
  }
}
