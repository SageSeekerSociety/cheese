// The composer half of the room panel: what has been typed, for which topic,
// and where it goes.
//
// Everything the input box holds belongs to the topic it was composed in — the
// text, the message being replied to, the images already uploaded to that
// topic's worktree, and the sends that have not landed yet. This module is that
// whole set, plus the two other things that read it: the built-in RoomComposer
// and the 起手区块 (starter prompts), which retire once 芝士 has spoken here.
//
// The fetch/socket half is `useChatPanel`; it hands over the few refs the
// composer must read (the window it is typed into) or write (the error banner,
// the awaiting-reply flag, the outbox restored into), and the `send` callback
// that actually posts. Nothing here touches the room socket.
import type { ComputedRef, Ref } from 'vue'
import type { Block, ChatAttachment, Topic } from '../cx_types'
import type { ComposerMemory, Outgoing, StoredComposerDraft } from '../lib/composerDrafts'
import type { NoticeRow } from '../lib/platformNotice'
import type { QuotedContext } from '../lib/quotedContext'

import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { editMessage, summonAgent } from '../api'
import { uploaded, usePendingAttachments } from '../lib/attachments'
import { isAgentBlock } from '../lib/authorship'
import { replySnippet } from '../lib/blockDisplay'
import { loadComposerDraft, loadComposerMemory, saveComposerDraft, saveComposerMemory } from '../lib/composerDrafts'
import { frozenQuote } from '../lib/quotedContext'
import { editableText } from '../lib/renderMessage'

import { t } from '@/i18n'

/** The handle→name / id→title maps the row renderer fills in (see useChatPanel). */
export interface RowRefs {
  mentionNames: Record<string, string>
  topicTitles: Record<string, string>
}

export interface ChatComposerDeps {
  topic: () => Topic | null
  /** The panel reads the room's own conversation, not a task or 支线 in it. A
   *  task's panel is handed the room as `topic` too. */
  ownLine: () => boolean
  alwaysSummon: () => boolean
  showComposer: () => boolean
  /** The rows the timeline is showing — the retry button only ever sits on the last one. */
  rows: ComputedRef<NoticeRow[]>
  blocks: ComputedRef<Block[]>
  hasMore: Ref<boolean>
  loadingHistory: Ref<boolean>
  awaitingReply: Ref<boolean>
  outbox: Ref<Outgoing[]>
  errorMsg: Ref<string | null>
  agentSeat: () => { label?: string } | undefined
  refs: RowRefs
  timeline: { find(id: string): Block | undefined; replace(block: Block): void }
  /** Client ids whose row is on its way out after an edit (see useChatPanel). */
  editing: Set<string>
  isMine: (m: Block) => boolean
  displayName: (m: Block) => string
  send: (content: string, summon: boolean, atts?: ChatAttachment[], quote?: QuotedContext) => boolean
  dropSend: (clientId: string) => void
}

export function useChatComposer(deps: ChatComposerDeps) {
  const {
    topic,
    ownLine,
    alwaysSummon,
    showComposer,
    rows,
    blocks,
    hasMore,
    loadingHistory,
    awaitingReply,
    outbox,
    errorMsg,
    agentSeat,
    refs,
    timeline,
    editing,
    isMine,
    displayName,
    send,
    dropSend,
  } = deps

  const replyTarget = ref<Block | null>(null)
  function setReply(m: Block) {
    replyTarget.value = m
  }
  function clearReply() {
    replyTarget.value = null
  }

  // ---- 改自己发过的消息：正文原地换成输入框，保存之后房间里每个人看到新的正文。 ----
  // 只有自己说的话：别人的、芝士的都不在此列。
  const editingId = ref<string | null>(null)
  const editSaving = ref(false)
  function canEdit(m: Block): boolean {
    return isMine(m) && m.kind === 'message'
  }
  function startEdit(m: Block) {
    editingId.value = m.id
  }
  async function saveEdit(m: Block, text: string) {
    const content = text.trim()
    if (editSaving.value || !content) return
    if (content === editableText(m.content, refs).trim()) {
      editingId.value = null
      return
    }
    editSaving.value = true
    try {
      const updated = await editMessage(m.id, content)
      timeline.replace(updated)
      if (editingId.value === m.id) editingId.value = null
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.message.saveFailed')
    } finally {
      editSaving.value = false
    }
  }
  // 输入框里那枚回复标签上写的：回复的是谁、那条说了什么。
  const replyLabel = computed(() =>
    replyTarget.value
      ? t('work.room.composer.replyTo', {
          name: displayName(replyTarget.value),
          text: replySnippet(replyTarget.value, refs),
        })
      : null
  )

  // 发送失败之后的「编辑」：这一条从发件箱里拿掉，原文、回复对象和附件放回输入框，
  // 改完再发就是一条新的。输入框里已经有字的话，原文放在前面，一个字都不覆盖。
  function editSend(item: Outgoing) {
    if (
      draftQuote.value &&
      item.quotedContext &&
      JSON.stringify(draftQuote.value) !== JSON.stringify(item.quotedContext)
    ) {
      errorMsg.value = t('slides.draftQuoteConflict')
      return
    }
    if (item.quotedContext) draftQuote.value = frozenQuote(item.quotedContext)
    editing.add(item.clientId)
    dropSend(item.clientId)
    draft.value = draft.value.trim() ? `${item.content}\n${draft.value}` : item.content
    const parent = item.replyTo ? timeline.find(item.replyTo) : undefined
    if (parent) replyTarget.value = parent
    if (item.atts?.length) pendingAtts.value = [...item.atts, ...pendingAtts.value]
    composerRef.value?.focus()
  }

  // ---- Self-contained composer (only when showComposer) ----
  const draft = ref('')
  const draftQuote = ref<QuotedContext>()
  function clearDraftQuote() {
    draftQuote.value = undefined
  }
  const composerRef = ref<{ focus: () => void } | null>(null)

  const starterPrompts = computed(() =>
    (['tidy', 'research', 'build', 'plan'] as const).map((key) => ({
      label: t(`work.room.chat.starter.${key}`),
      hint: t(`work.room.chat.starter.${key}Hint`),
      text: t(`work.room.chat.starter.${key}Text`),
    }))
  )
  // 起手区块什么时候退休：芝士在这个房间里说过第一句话之后。
  //
  // 退休判据**不是「房间里有东西」**。平台自己发的公告、赛题报名写进去的简报、
  // 用户对着同事说的那几句，都能把房间填满，但一件都不能替代「跟芝士说上话」这
  // 件事本身；照旧判据，新用户只要先说了句没 @ 的话，这个入口就没了，而他要找的
  // 恰恰是「我该跟它说什么」。
  //
  // 只看 message / attachment：芝士也可能留下 event 行（「芝士处理中」那类），
  // 那是它干活的过程，不是它对这个人开过口。
  const startersRetired = computed(() =>
    blocks.value.some((b) => isAgentBlock(b) && (b.kind === 'message' || b.kind === 'attachment'))
  )

  const showStarters = computed(
    () =>
      ownLine() &&
      topic()?.kind === 'root' &&
      topic()?.status !== 'archived' &&
      showComposer() &&
      !loadingHistory.value &&
      !errorMsg.value &&
      !hasMore.value &&
      !startersRetired.value &&
      // 正在回话也先收起来：这一轮已经开了，芝士的答复落地之后由 `startersRetired`
      // 接手。两者中间不留一条缝——不然刚 @ 完、还没等到回话的那几秒里，起手区块
      // 会闪一下。
      !awaitingReply.value &&
      !draft.value.trim() &&
      !outbox.value.length
  )

  function startDraft(text: string) {
    if (draft.value.trim()) return
    // 起手草稿里那个 @ 和按钮写进去的是同一个名字（见 `agentSeat`）：写错了的话，
    // 人点完一张起手卡发出去，屋里会动的那位不动，而草稿上明明 @ 着「芝士」。
    const agent = agentSeat()
    draft.value = `${alwaysSummon() ? '' : `@${agent?.label ?? t('work.room.defaultAgentName')} `}${text}`
    composerRef.value?.focus()
  }

  const {
    pending: pendingAtts,
    uploading: attsUploading,
    addFiles,
    addLibraryFile,
    onPaste: onComposerPaste,
    onDrop: onComposerDrop,
    removeAt: removePendingAtt,
    retry: retryPendingAtt,
    clear: clearPendingAtts,
  } = usePendingAttachments(
    () => topic()?.id,
    (msg) => {
      errorMsg.value = msg
    }
  )

  // 失败提示上的「重试」。只给最新的那一条：更早的失败已经被后面发生的事盖过去了，
  // 在它上面重试说不清是在重试什么。房间在跑、归档了，重试都没有意义。
  function canRetryAt(i: number): boolean {
    if (i !== rows.value.length - 1) return false
    if (!showComposer() || topic()?.status === 'archived') return false
    return !awaitingReply.value && !outbox.value.length
  }
  // 重试走的是「交给它」同一个入口：失败的那一轮没有把消息标成已读，所以它们还在
  // 等人处理，平台重新开一轮去接。什么都没有可接的时候要说出来，不能按了没反应。
  // 请求还没回来时再按一次就是再开一轮，所以按着的时候按钮是忙的。
  const retryBusy = ref(false)
  async function retryNow() {
    const id = topic()?.id
    if (!id || retryBusy.value) return
    retryBusy.value = true
    try {
      const res = await summonAgent(id)
      if (res.started) awaitingReply.value = true
      else if (res.reason === 'nothing_pending') errorMsg.value = t('work.room.retry.nothingPending')
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.retry.failed')
    } finally {
      retryBusy.value = false
    }
  }

  // 输入区只知道正文和「这条叫不叫它」。待发附件在这一层，因为它要跟着话题走。
  //
  // 有一枚没传上去就整条挡住：`uploaded()` 会把它跳过，发出去的话那一段话自带一个
  // 说得出口的理由（「带了 3 个附件」）却只带上去 2 个，而人不会知道少了哪个。挡住
  // 并说清怎么办，比悄悄少发一个安全。
  function onComposerSend({ content, summon }: { content: string; summon: boolean }) {
    if (pendingAtts.value.some((a) => a.error)) {
      errorMsg.value = t('work.room.attachments.sendBlocked')
      return
    }
    if (send(content, summon, uploaded(pendingAtts.value), draftQuote.value)) {
      draft.value = ''
      clearDraftQuote()
      clearPendingAtts()
    }
  }

  // 每话题草稿: stash / restore everything the composer holds. Nothing here is
  // "just a preference" — each field names something in the topic being left
  // (a block to reply to, files already uploaded to that topic's worktree).
  // 每话题草稿 (飞书语义): what you had typed, who you were replying to, and the
  // images waiting to go — all belong to the topic they were composed in.
  //
  // 之前只有待发图片被清掉，文字和回复目标原地不动地跟着你换话题：打了一半的话
  // 可能发错房间，而**回复目标**更糟——它指向的块在另一个话题里，屏幕上看不出
  // 异常（本话题找不到父块就不画引用条），库里的会话树已经串了。
  //
  // 存在哪、分几层、谁清它，全在 lib/composerDrafts.ts —— 这里只有调用。那个 Map
  // 一度住在这个文件里，于是同一个概念有两套规则，换账号只清掉了其中一套。
  function rememberComposer(topicId: string) {
    // 两层各写一次，都不在这里判空——两层的「空」本来就不是同一个定义（内存那层还
    // 管着发件箱），各自判各自的。在这里判一次再分发，等于替它们决定，而那个判据
    // 只可能对其中一层是对的。
    saveComposerMemory(topicId, {
      draft: draft.value,
      quotedContext: draftQuote.value,
      reply: replyTarget.value,
      atts: uploaded(pendingAtts.value),
      outbox: outbox.value.slice(),
    })
    // 落到磁盘上的那份不含发件箱，见 lib/composerDrafts.ts 的解释。
    saveComposerDraft(topicId, {
      draft: draft.value,
      quotedContext: draftQuote.value,
      reply: replyTarget.value,
      atts: uploaded(pendingAtts.value),
    })
  }

  /** 落盘的那份没有发件箱（它不跨刷新，也不该跨）。 */
  function asComposerDraft(stored: StoredComposerDraft | null): ComposerMemory | undefined {
    return stored
      ? { draft: stored.draft, quotedContext: stored.quotedContext, reply: stored.reply, atts: stored.atts, outbox: [] }
      : undefined
  }

  function restoreComposer(topicId: string | undefined) {
    // 内存里那一份优先：它带着发件箱。只有它不在时（刚刷新过、刚开机）才回落到
    // 磁盘上那份。
    const saved = topicId ? loadComposerMemory(topicId) ?? asComposerDraft(loadComposerDraft(topicId)) : undefined
    draft.value = saved?.draft ?? ''
    draftQuote.value = saved?.quotedContext
    replyTarget.value = saved?.reply ?? null
    pendingAtts.value = saved?.atts ?? []
    // 换话题时在飞的那一条被叫停了，回到队列，回到这个话题时带同一个 id 再发
    // （后端认得它，不会落两遍）。它们不会在别的房间里露面。
    outbox.value = (saved?.outbox ?? []).map((o) => (o.state === 'sending' ? { ...o, state: 'queued' } : o))
  }

  // 边打边落盘。刷新是唯一会丢草稿的路径，而它**不会**经过 rememberComposer
  // （那个跑在切话题和卸载时）——所以输入本身也要定期存一次。800ms 是打字停顿的
  // 量级；localStorage 是同步的，写一次的成本就是这次停顿。
  let draftSaveTimer: ReturnType<typeof setTimeout> | null = null
  function flushComposer(topicId: string) {
    if (draftSaveTimer) {
      clearTimeout(draftSaveTimer)
      draftSaveTimer = null
    }
    saveComposerDraft(topicId, {
      draft: draft.value,
      quotedContext: draftQuote.value,
      reply: replyTarget.value,
      atts: uploaded(pendingAtts.value),
    })
  }

  watch(
    [draft, draftQuote, replyTarget, pendingAtts],
    () => {
      const topicId = topic()?.id
      if (!topicId) return
      if (draftSaveTimer) clearTimeout(draftSaveTimer)
      draftSaveTimer = setTimeout(() => {
        draftSaveTimer = null
        // 停了 800ms 之后当前话题可能已经换了：那样这一笔该记在旧话题上，而旧话题
        // 走的是 rememberComposer，不差这一下。
        if (topic()?.id === topicId) flushComposer(topicId)
      }, 800)
    },
    { deep: false }
  )

  // 页面被切到后台 / 关掉之前最后记一次：手机上的标签页可以被直接丢掉，不一定会
  // 走 onBeforeUnmount。用的是同步写，来得及。
  function flushComposerOnHide() {
    const room = topic()
    if (room) flushComposer(room.id)
  }
  useEventListener(window, 'pagehide', flushComposerOnHide)
  useEventListener(document, 'visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushComposerOnHide()
  })

  return {
    draft,
    draftQuote,
    clearDraftQuote,
    composerRef,
    starterPrompts,
    showStarters,
    startDraft,
    pendingAtts,
    attsUploading,
    addFiles,
    addLibraryFile,
    onComposerPaste,
    onComposerDrop,
    removePendingAtt,
    retryPendingAtt,
    clearPendingAtts,
    replyTarget,
    setReply,
    clearReply,
    replyLabel,
    editingId,
    editSaving,
    canEdit,
    startEdit,
    saveEdit,
    editSend,
    canRetryAt,
    retryBusy,
    retryNow,
    onComposerSend,
    rememberComposer,
    restoreComposer,
    flushComposer,
  }
}
