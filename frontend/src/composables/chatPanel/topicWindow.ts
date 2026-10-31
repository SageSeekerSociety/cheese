// 换一段对话时做的事：把上一条留下的东西收干净，再读一窗新的历史。
//
// 读的是「一屏」而不是整条时间线（更早的几页等往上翻时再读，见 useChatPaging）；切走
// 再回来时，缓存里那一份先摆上，最新的那一页回来再合进去。换、回来的那一下（`watch`）
// 和卸载时的收尾也在这里：它们和这一窗是同一件事的两端。
import type { Ref } from 'vue'
import type { useChatRowActions } from '../../components/chat/composables/useChatRowActions'
import type { useTimelineMotion } from '../../components/chat/composables/useTimelineMotion'
import type { useHistoryReads } from '../../components/room/composables/useHistoryReads'
import type { useLiveSteps } from '../../components/room/composables/useLiveSteps'
import type { useRoomActivity } from '../../components/room/composables/useRoomActivity'
import type { useRoomTail } from '../../components/room/composables/useRoomTail'
import type { useRoomTurns } from '../../components/room/composables/useRoomTurns'
import type { useRowBatch } from '../../components/room/composables/useRowBatch'
import type { useRunRecords } from '../../components/room/composables/useRunRecords'
import type { useTimeline } from '../../components/room/composables/useTimeline'
import type { useTypingPreview } from '../../components/room/composables/useTypingPreview'
import type { Block, Topic } from '../../cx_types'
import type { Outgoing } from '../../lib/composerDrafts'
import type { useChatComposer } from '../useChatComposer'
import type { useChatPaging } from '../useChatPaging'
import type { useMessageReactions } from '../useMessageReactions'

import { onBeforeUnmount, watch } from 'vue'

import { ApiError, ensureFreshToken, isRetryableGetFailure } from '../../api'
import { applyLiveChanges } from '../../lib/blockPaging'

import { t } from '@/i18n'
import { cachedWindow, forgetCachedWindow, readNewestBlocks, setCachedWindow } from '@/query/blocks'

/** 打开一段对话：清干净、摆上缓存、读最新一页，并看着它换。 */
export function usePanelTopicWindow(deps: {
  /** 这一栏此刻读的那段对话（房间自己，或房间里的一个任务）。 */
  place: () => Topic | null
  /** 地址点名的那条消息。 */
  focusBlock: () => string | null
  /** 卸下之后还在飞的那几个请求不该再写回来。 */
  disposed: Ref<boolean>
  timeline: ReturnType<typeof useTimeline>
  history: ReturnType<typeof useHistoryReads>
  tail: ReturnType<typeof useRoomTail>
  runRecords: ReturnType<typeof useRunRecords>
  turns: ReturnType<typeof useRoomTurns>
  rowBatch: ReturnType<typeof useRowBatch>
  reactions: ReturnType<typeof useMessageReactions>
  errorMsg: Ref<string | null>
  loadingHistory: Ref<boolean>
  unseen: Ref<string[]>
  /** 点了「编辑」的那几条：换一段对话时清空，别带到下一间房。 */
  editing: Set<string>
  /** 未读线钉在哪条上（usePanelRows）。 */
  unreadAnchorId: Ref<string | null>
  placeUnreadAnchor: () => void
  atBottom: Ref<boolean>
  autoScroll: () => void
  restoreScroll: (topicId: string) => void
  rememberScroll: (topicId: string | undefined) => void
  /** socket 那边（usePanelSocket）。 */
  connectSocket: (topicId: string) => void
  closeSocket: () => void
  connectRefused: Ref<boolean>
  retryLater: (topicId: string) => void
  /** 发件箱（usePanelOutgoing）。 */
  outbox: Ref<Outgoing[]>
  settleOutbox: (block: Block) => unknown
  flushOutbox: () => void
  pauseOutbox: () => void
  /** 下面这几块这时还没建出来（它们反过来依赖这一窗），用取值函数晚一步拿。 */
  activity: () => ReturnType<typeof useRoomActivity>
  liveSteps: () => ReturnType<typeof useLiveSteps>
  typing: () => ReturnType<typeof useTypingPreview>
  rowActions: () => ReturnType<typeof useChatRowActions>
  motion: () => ReturnType<typeof useTimelineMotion>
  composer: () => ReturnType<typeof useChatComposer>
  paging: () => ReturnType<typeof useChatPaging>
}) {
  function historyReadFailed(roomId: string, e: unknown) {
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
      deps.closeSocket()
      // 已经看不到这间房了：先摆上的那份缓存（可能是上次从浏览器里放回来的）撤掉。
      forgetCachedWindow(roomId)
      deps.timeline.show({ blocks: [], hasMore: false })
    }
    deps.errorMsg.value = e instanceof Error ? e.message : t('work.room.loadFailed')
    // A failed history fetch must not terminate socket recovery during an outage.
    if (isRetryableGetFailure('GET', e instanceof ApiError ? e.status : undefined, e)) deps.retryLater(roomId)
  }

  async function loadTopic(room: Topic, entering = false) {
    const { changes, reactions, stillHere, done, end } = deps.history.begin(room.id, true)
    // 地址点名了一条消息：落到它上面，而不是上次停的地方。
    const focus = deps.focusBlock() ?? null
    deps.errorMsg.value = null
    deps.connectRefused.value = false // a fresh topic gets a fresh attempt at connecting
    deps.turns.reset()
    deps.typing().clear()
    deps.activity().reset()
    deps.liveSteps().reset()
    deps.runRecords.reset()
    deps.reactions.reactionPickerFor.value = null
    deps.rowActions().resetBar()
    deps.unreadAnchorId.value = null
    deps.motion().arrived.clear()
    deps.motion().sentNow.clear()
    deps.motion().delivered.clear()
    deps.editing.clear()
    deps.composer().editingId.value = null
    deps.unseen.value = []
    deps.tail.reset()
    deps.composer().clearPendingAtts() // pending images belong to the topic they were typed in
    deps.closeSocket()
    deps.paging().loadingOlder.value = false
    deps.rowBatch.revealAll()
    const cached = cachedWindow(room.id)
    if (cached) {
      deps.timeline.show(cached)
      deps.rowBatch.startFor(room.id, focus)
      if (!focus) deps.restoreScroll(room.id)
      // 缓存里只有一页，高屏上一页短消息可能画不满（一条消息飘在半空、下面空一片）。
      // 不等上面那条请求回来，现在就往回补：补的时候滚动补偿把最新那条钉在底部，历史
      // 往上长。见 useChatPaging。
      void deps.paging().fillViewportIfNeeded()
    } else {
      deps.timeline.show({ blocks: [], hasMore: false })
      deps.loadingHistory.value = true
    }
    try {
      // Allow composer restoration to finish, then authenticate both transports.
      // Recovery keeps its history-first reconciliation for lost message echoes.
      await ensureFreshToken()
      if (!stillHere()) return
      const parallelSocket = entering && deps.outbox.value.length === 0
      if (parallelSocket) deps.connectSocket(room.id)
      // 打开话题的那次导航已经替它起了头（router/index.ts）：这里跟着那一条走，不另发
      // 一次。回来的是缓存窗口接上最新一页之后的样子（`query/blocks`）。
      // One screenful, not the whole timeline — older blocks arrive when the
      // user scrolls up to them (loadOlder).
      // 断线重连回来再开一次的（不是刚进房间）：之前那次读是断线前发出的，可能卡在
      // 半路，不跟着它等，另问一次。
      const fresh = await readNewestBlocks(room.id, { restart: !entering })
      // Only apply if still the active topic (avoid race on fast switching).
      if (!stillHere()) return
      for (const block of fresh.blocks) deps.tail.hold(block)
      // Blocks that landed while we were away append at the tail; if the user
      // was parked at the bottom, follow them so the newest message is visible
      // without a manual scroll. Compared on the LAST id, not on length: the
      // cached window and this page can be different sizes (the user may have
      // paged back), so a length comparison says nothing about the tail.
      const shown = cached
      // 开场补窗（上面那次 fillViewportIfNeeded）可能已经把更早的几页读进来了。游标是
      // 「窗口读到哪了」，比缓存窗口的更老就说明窗口被往上撑开过——这时候下面这一份
      // 「最新一页」只是它的尾巴，`timeline.show` 整段换会把刚读回来的历史丢掉、屏幕跳
      // 回最新那条。改成把它当缓存窗口，按它去合并。
      const extended = shown !== null && deps.timeline.oldestLoaded() !== (shown.blocks[0]?.id ?? null)
      const grew = shown !== null && shown.blocks.at(-1)?.id !== fresh.blocks.at(-1)?.id
      // Merged rather than replaced (in `readNewestBlocks`), so scrollback the user
      // already loaded (and that restoreScroll's saved offset refers to) does not
      // vanish under them.
      const merged = { ...fresh }
      // Live frames can arrive while the HTTP snapshot is pending. Apply them
      // last, including retractions, so that snapshot cannot erase newer events.
      merged.blocks = applyLiveChanges(merged, changes, reactions)
      if (extended) {
        // 窗口已经被补开了：不整段换，逐条接——变了的原地替换，新来的接到末尾。更早
        // 那段历史原样留着，滚动位置也就不动。
        for (const block of merged.blocks) {
          if (deps.timeline.find(block.id)) deps.timeline.replace(block)
          else deps.timeline.append(block)
        }
      } else {
        deps.timeline.show(merged)
      }
      // A reconnect starts with durable history. Settle sends that landed while
      // their echo was lost before opening the new socket; only absent client ids
      // remain queued for an idempotent resend.
      for (const block of merged.blocks) deps.settleOutbox(block)
      // 补过窗的话缓存那一页由翻页自己维护（pullOlderPage 会写），这里别用这一页盖回去。
      if (!extended) setCachedWindow(room.id, merged)
      deps.placeUnreadAnchor() // 冻在这一刻：之后来的新消息不再移动这条线
      if (!shown) deps.rowBatch.startFor(room.id, focus)
      if (focus) void deps.paging().openAt(focus)
      else if (!shown) deps.restoreScroll(room.id)
      else if (grew && deps.atBottom.value) deps.autoScroll()
      if (!parallelSocket && !deps.connectRefused.value) deps.connectSocket(room.id)
      // 开场这一窗先补满一屏，再让骨架撤（下面 finally 才把 loadingHistory 落下）：补完
      // 之前露出来就是「一条消息飘在半空」。见 useChatPaging。
      await deps.paging().fillViewportIfNeeded()
      done()
    } catch (e) {
      if (stillHere()) historyReadFailed(room.id, e)
    } finally {
      if (end()) {
        deps.loadingHistory.value = false
        deps.tail.check()
      }
    }
  }

  // IME (输入法) guard — see TopicView.vue for the full story: Safari fires
  // compositionend BEFORE the commit-Enter keydown, which then looks like a
  // plain Enter. Track composition ourselves and swallow the trailing Enter.

  watch(
    () => deps.place()?.id,
    (id, oldId) => {
      // Save where we were in the topic we're leaving, so coming back restores it.
      if (oldId) deps.rememberScroll(oldId)
      if (oldId) {
        deps.composer().rememberComposer(oldId)
        deps.pauseOutbox()
      }
      const room = deps.place()
      if (room) {
        // loadTopic clears the pending attachments synchronously before its first
        // await, so this topic's own draft has to be restored AFTER the call.
        void loadTopic(room, true)
        deps.composer().restoreComposer(id)
        deps.flushOutbox() // 上次在这个房间里没送完的，接着送
      } else {
        deps.timeline.show({ blocks: [], hasMore: false })
        deps.closeSocket()
      }
    },
    { immediate: true }
  )

  onBeforeUnmount(() => {
    deps.disposed.value = true
    // persist position across an unmount (e.g. leaving the view)
    deps.rememberScroll(deps.place()?.id)
    const room = deps.place()
    if (room) deps.composer().rememberComposer(room.id)
    // 链路和回声计时器由各自的 composable 在 scope 停掉时收，这里不重复一遍。
  })

  return { loadTopic, historyReadFailed }
}
