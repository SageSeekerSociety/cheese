/**
 * 「这个话题里你写了什么」——两层存储，一个家。
 *
 * 两层各自答一个问题，谁也替不了谁：
 *
 * - **内存层**（下面的 `composerMemory`）跨「切话题」。它带着发件箱，所以它是权威。
 * - **磁盘层**（localStorage）跨「刷新」。它不带发件箱，理由见下。
 *
 * 这两层一度分居：磁盘这半在本文件，内存那半是 `ChatPanel.vue` 里一个模块级 Map。
 * 于是同一个概念有两套规则——这半有 20 个话题上限、7 天过期、单条长度上限、
 * 换人登录清空，那半一条都没有。**而 `restoreComposer` 先读内存**，也就是说规则严的
 * 那份是备胎、没规则的那份是权威：`clearComposerDrafts()` 在换账号时清掉了磁盘，
 * 内存里上一个人的半句话、回复目标、已上传附件和没发出去的消息原样留着，
 * 下一个人打开同一个房间就看得见（SPA 换账号不刷新页面）。
 * 所以两层现在住在一起，清理入口只有一个，不会再只清一半。
 *
 * 为什么磁盘这层非有不可：**每次发版，开着的页面都会自己刷新**
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

/** 发件箱里一条还没落库的消息。 */
export interface Outgoing {
  clientId: string
  content: string
  replyTo?: string
  atts?: ChatAttachment[]
  /** queued = 还没送出去（没连上）; sending = 送出了在等回声; failed = 等超了 */
  state: 'queued' | 'sending' | 'failed'
  /** 服务端明确拒了这一条时它说的话。没有这一句的 failed 是「等超了」。 */
  error?: string
}

/** 内存层存的一份：磁盘那份的全部，加上发件箱。 */
export interface ComposerMemory {
  draft: string
  reply: Block | null
  atts: ChatAttachment[]
  /** 还没落库的消息。它们是发给**这个**话题的，跟着它走，不跟着屏幕走。 */
  outbox: Outgoing[]
}

/**
 * 内存层。模块作用域而不是组件内的 ref：对话栏会因为切话题、切桌面/手机布局、
 * 去别的页面再回来而卸载重建，组件内的状态装不住「你在那个房间里写了什么」。
 *
 * 没有条数上限和过期——它跟着这一次页面会话一起没，而一次会话里开过的房间数
 * 本来就是有限的。真正需要上限的是磁盘那层（localStorage 只有几 MB）。
 */
const composerMemory = new Map<string, ComposerMemory>()

/** 空的等于没有：正文、回复目标、附件、发件箱全空就删掉这条记录。 */
function memoryIsEmpty(value: ComposerMemory): boolean {
  return !value.draft.trim() && !value.reply && !value.atts.length && !value.outbox.length
}

/** 记下这个话题的输入状态（含发件箱）。 */
export function saveComposerMemory(topicId: string, value: ComposerMemory): void {
  const id = topicId.trim()
  if (!id) return
  if (memoryIsEmpty(value)) composerMemory.delete(id)
  else composerMemory.set(id, value)
}

/** 取回这个话题的输入状态；没写过就是 undefined。 */
export function loadComposerMemory(topicId: string): ComposerMemory | undefined {
  const id = topicId.trim()
  return id ? composerMemory.get(id) : undefined
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

/**
 * 忘掉磁盘上那份（发出去了、或者被清空了）。
 *
 * **只管磁盘这一层。** 两层的「空」不是同一个定义：磁盘那份不存发件箱，所以
 * 「正文和附件都空了」对它就是空，而那一刻内存里可能还压着几条没送出去的消息。
 * `saveComposerDraft` 判到空会自己调这里，让它顺手清内存，等于消息一发出去
 * 发件箱就没了。要一次清干净两层的只有 `clearComposerDrafts`（换人的时候）。
 */
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

/**
 * 抹掉全部草稿。换人登录 / 退出登录时调用（account.ts）。
 *
 * **两层都要清。** 只清磁盘那层等于没清：SPA 换账号不刷新页面，内存层活着，
 * 而 `loadComposerMemory` 排在 `loadComposerDraft` 前面——下一个人打开同一个房间，
 * 看到的是上一个人的半句话。
 */
export function clearComposerDrafts(): void {
  composerMemory.clear()
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
