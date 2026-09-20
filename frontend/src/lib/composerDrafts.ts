/**
 * 输入框草稿的落盘（localStorage）。
 *
 * 为什么不只是 ChatPanel 里那个内存 Map：**每次发版，开着的页面都会自己刷新**
 * （service worker 换新 → `window.location.reload()`，见 pwa.ts）。刷新没有
 * 「离开这个话题」这一步，`onBeforeUnmount` / 切话题那个 watcher 一个都不会跑，
 * 于是打了一半的话在用户眼皮底下消失。草稿落盘是让「更新会自动发生」成立的前提。
 *
 * 存在这里的是**属于某个话题的输入状态**，不是偏好设置：正文、回复目标、还有
 * 已经传上去、等着跟下一条消息一起发出去的附件。附件存的是服务端引用
 * (`{path, mime}`)，不含令牌——它的 URL 是发送/渲染时现拼的。
 *
 * **发件箱（还没落库的消息）不在其中**，这是有意的：`flushOutbox()` 会把
 * queued / failed 的条目重新交给 socket，也就是说存下来的东西会在下次打开时**被
 * 自动发出去**。隔一天开机把昨天那句没送出去的话发出去，是比丢草稿更糟的意外。
 * 它在会话内的行为（切话题时跟着话题走）不变，只是不跨刷新。
 *
 * 键里只有话题 id，没有用户——话题 id 是全站共享的。换人登录时由
 * `clearComposerDrafts()` 抹掉（account.ts 的两处：换人登录、退出登录），
 * 和 service worker 的 API 读缓存、页面缓存同一个规矩。
 */

import type { Block, ChatAttachment } from '@/cx_types'

const KEY_PREFIX = 'cheese.composer.v1:'

/** 攒了太久没发出去的草稿不值得再弹回来：一周。 */
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

/** 最多替几个话题记着，按最后写入时间淘汰。 */
const MAX_TOPICS = 20

/**
 * 单条记录的上限（字符数，按序列化后的长度算）。草稿里可能粘进来一整个文件，
 * localStorage 只有几 MB 且写满会抛异常——超了就不存，界面上一个字都不会少。
 */
const MAX_CHARS = 200_000

export interface StoredComposerDraft {
  /** 写入时刻，用来判过期和淘汰。 */
  savedAt: number
  draft: string
  reply: Block | null
  atts: ChatAttachment[]
}

function storageKey(topicId: string): string | null {
  const id = topicId.trim()
  return id ? `${KEY_PREFIX}${encodeURIComponent(id)}` : null
}

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    // 站点数据被禁用时连读这个属性都会抛。
    return null
  }
}

function validAttachment(value: unknown): value is ChatAttachment {
  if (typeof value !== 'object' || value === null) return false
  const att = value as { path?: unknown; mime?: unknown }
  return typeof att.path === 'string' && typeof att.mime === 'string'
}

/** 只信我们自己写下去的、且还认得出来的那些字段。 */
function parse(raw: string, now: number): StoredComposerDraft | null {
  const parsed = JSON.parse(raw) as Partial<StoredComposerDraft>
  if (typeof parsed?.savedAt !== 'number' || now - parsed.savedAt > MAX_AGE_MS) return null
  if (typeof parsed.draft !== 'string') return null
  const reply = parsed.reply ?? null
  if (reply !== null && typeof reply !== 'object') return null
  const atts = Array.isArray(parsed.atts) ? parsed.atts.filter(validAttachment) : []
  return { savedAt: parsed.savedAt, draft: parsed.draft, reply: reply as Block | null, atts }
}

/** 记着的话题太多时，把最旧的几条丢掉（连带过期的）。 */
function prune(store: Storage, now: number) {
  const entries: { key: string; savedAt: number }[] = []
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i)
    if (!key?.startsWith(KEY_PREFIX)) continue
    let savedAt = 0
    try {
      const parsed = JSON.parse(store.getItem(key) ?? '') as { savedAt?: unknown }
      savedAt = typeof parsed?.savedAt === 'number' ? parsed.savedAt : 0
    } catch {
      // 读不出来的当最旧，顺手清掉。
    }
    if (now - savedAt > MAX_AGE_MS) store.removeItem(key)
    else entries.push({ key, savedAt })
  }
  entries.sort((a, b) => a.savedAt - b.savedAt)
  for (const entry of entries.slice(0, Math.max(0, entries.length - MAX_TOPICS))) store.removeItem(entry.key)
}

/**
 * 存一份草稿。空草稿（没有正文、没有回复目标、没有附件）等于「这里没有东西」，
 * 直接删掉这条记录——不能让一条空记录把上次的草稿顶掉。
 */
export function saveComposerDraft(
  topicId: string,
  draft: Pick<StoredComposerDraft, 'draft' | 'reply' | 'atts'>,
  now: number = Date.now()
): void {
  const key = storageKey(topicId)
  const store = storage()
  if (!key || !store) return
  const empty = !draft.draft.trim() && !draft.reply && !draft.atts.length
  if (empty) {
    forgetComposerDraft(topicId)
    return
  }
  try {
    const record: StoredComposerDraft = {
      savedAt: now,
      draft: draft.draft,
      reply: draft.reply,
      atts: draft.atts,
    }
    const serialized = JSON.stringify(record)
    if (serialized.length > MAX_CHARS) return
    store.setItem(key, serialized)
    prune(store, now)
  } catch {
    // 写满了、被禁用了、或者草稿本身序列化不了 —— 都不该让输入框出问题。
  }
}

/** 取回话题的草稿；没有、过期、或者读不懂时返回 null。 */
export function loadComposerDraft(topicId: string, now: number = Date.now()): StoredComposerDraft | null {
  const key = storageKey(topicId)
  const store = storage()
  if (!key || !store) return null
  try {
    const raw = store.getItem(key)
    if (!raw) return null
    const parsed = parse(raw, now)
    if (!parsed) store.removeItem(key)
    return parsed
  } catch {
    store.removeItem(key)
    return null
  }
}

/** 忘掉一个话题的草稿（发出去了、或者被清空了）。 */
export function forgetComposerDraft(topicId: string): void {
  const key = storageKey(topicId)
  const store = storage()
  if (!key || !store) return
  try {
    store.removeItem(key)
  } catch {
    // 同上：存储不可用时静默跳过。
  }
}

/** 抹掉全部草稿。换人登录 / 退出登录时调用（account.ts）。 */
export function clearComposerDrafts(): void {
  const store = storage()
  if (!store) return
  try {
    const keys: string[] = []
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i)
      if (key?.startsWith(KEY_PREFIX)) keys.push(key)
    }
    for (const key of keys) store.removeItem(key)
  } catch {
    // 同上。
  }
}
