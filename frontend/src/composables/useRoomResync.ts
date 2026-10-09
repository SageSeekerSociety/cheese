/**
 * 断线重连后，房间怎么在原地对上服务端：屏幕上一直留着断线前的那一份，这里只做
 * 就地的增删改。进房间才清空（useChatPanel 的 loadTopic），那时屏幕上是别的房间。
 *
 * 两半：
 * - 时间线：读回来的最新一页合进屏幕上的窗口（变了的换掉、新来的接上、断线期间
 *   撤回的拿走），对齐方法见 lib/tailResync。
 * - 现场：服务端回答 `sync` 的 `room_state` 帧是此刻在跑的全部轮次和在忙的全部
 *   成员。断线期间结束了的轮次、停下的人、过去了的那一步、写到一半的消息在这里撤掉，
 *   其余原样留着。
 */

import type { Ref } from 'vue'
import type { useHistoryReads } from '../components/room/composables/useHistoryReads'
import type { useLiveSteps } from '../components/room/composables/useLiveSteps'
import type { useRoomActivity } from '../components/room/composables/useRoomActivity'
import type { useRoomTurns } from '../components/room/composables/useRoomTurns'
import type { useRunRecords } from '../components/room/composables/useRunRecords'
import type { useTimeline } from '../components/room/composables/useTimeline'
import type { useTypingPreview } from '../components/room/composables/useTypingPreview'
import type { Block } from '../cx_types'
import type { BlockWindow } from '../lib/blockPaging'
import type { RoomStateFrame } from '../types/roomSocket'

import { ensureFreshToken, listBlocks } from '../api'
import { setCachedWindow } from '../queries/blocks'
import { applyLiveChanges, PAGE_SIZE } from '../lib/blockPaging'
import { resyncTail } from '../lib/tailResync'

/**
 * 断线重连：屏幕上正是这间房，什么都不清。读回最新一页就地合进时间线，再开 socket；
 * 现场等连上后的 room_state 核对。输入框、待发的图片、正在编辑的那条、未读线、滚动
 * 位置是读的人自己的，一样不动。
 *
 * 补读（`catchUp`）是同一件事去掉断线的那一半：连接好好的，只是读的那一页早于订阅
 * 生效，中间落下的那条两头都没带来（useChatPanel 的 checkTail）。socket 不动，排队
 * 中的轮次也照旧——它们没错过什么。
 */
export function useRoomResync(room: {
  history: ReturnType<typeof useHistoryReads>
  timeline: ReturnType<typeof useTimeline>
  runRecords: ReturnType<typeof useRunRecords>
  /** 读回来的块里有断线期间没收到回执的自己发的那条：发件箱据此收尾。 */
  settle: (block: Block) => void
  /** 读回来的最新一页原样有哪些块（不露面的也算）。 */
  noteRead: (blocks: Block[]) => void
  /** 这一次读结束了（不管成没成）。 */
  readEnded: () => void
  scroll: {
    atBottom: Ref<boolean>
    /** 跟到最新：来了新的而读的人停在底部时。 */
    follow: () => void
    /** 一屏没画满就往回补（最新一带常常整页不露面）。 */
    fill: () => Promise<unknown>
  }
  socket: {
    /** 开 socket（连接被拒过就不开）。 */
    connect: (topicId: string) => void
    close: () => void
  }
  /** 读失败：说出来，能重试的排一次重连。 */
  failed: (topicId: string, error: unknown) => void
}) {
  /** 读最新一页并就地合进来；读到了（还在这间房）答 true。 */
  async function readTail(
    topicId: string,
    read: ReturnType<typeof room.history.begin>,
    afterOutage: boolean
  ): Promise<boolean> {
    await ensureFreshToken()
    if (!read.stillHere()) return false
    const payload = await listBlocks(topicId, { limit: PAGE_SIZE })
    if (!read.stillHere()) return false
    room.noteRead(payload.data)
    const fresh = { blocks: payload.data, hasMore: !!payload.has_more }
    fresh.blocks = applyLiveChanges(fresh, read.changes, read.reactions)
    // 排队中的那几轮核对不了：断线期间可能已经跑完了。
    if (afterOutage) room.runRecords.forgetWaiting()
    const grew = mergeResyncedPage(room.timeline, room.runRecords, fresh)
    for (const block of fresh.blocks) room.settle(block)
    setCachedWindow(topicId, room.timeline.newest())
    if (grew && room.scroll.atBottom.value) room.scroll.follow()
    return true
  }

  async function resync(topicId: string) {
    const read = room.history.begin(topicId)
    room.socket.close()
    try {
      if (!(await readTail(topicId, read, true))) return
      room.socket.connect(topicId)
      void room.scroll.fill()
    } catch (e) {
      if (read.stillHere()) room.failed(topicId, e)
    } finally {
      if (read.end()) room.readEnded()
    }
  }

  async function catchUp(topicId: string) {
    const read = room.history.begin(topicId)
    try {
      await readTail(topicId, read, false)
    } catch (e) {
      if (read.stillHere()) room.failed(topicId, e)
    } finally {
      if (read.end()) room.readEnded()
    }
  }

  return { resync, catchUp }
}

/** 把重连后读回来的最新一页合进时间线。返回最新的那一块换没换（来了新的）。 */
export function mergeResyncedPage(
  timeline: ReturnType<typeof useTimeline>,
  runRecords: ReturnType<typeof useRunRecords>,
  fresh: BlockWindow
): boolean {
  // 断线期间开始又结束的轮次，排队那一行要知道它已经开始过了：它在历史里留下的块
  // 带着轮次 id。
  for (const block of fresh.blocks) if (block.turn_id) runRecords.turnBegan(block.turn_id)
  const newestBefore = timeline.newest().blocks.at(-1)?.id
  const plan = resyncTail(timeline.newest().blocks, fresh, timeline.newestSeenAt())
  if (plan.gap) {
    // 断线期间来了不止一页：中间那截没读过，接上会留一个看不见的洞。
    timeline.show(fresh)
  } else {
    for (const id of plan.removed) timeline.remove(id)
    for (const block of plan.upserts) upsert(timeline, block)
  }
  return timeline.newest().blocks.at(-1)?.id !== newestBefore
}

function upsert(timeline: ReturnType<typeof useTimeline>, block: Block) {
  if (timeline.newest().blocks.some((b) => b.id === block.id)) timeline.replace(block)
  else timeline.append(block)
}

/** `room_state`：拿此刻的全部现场核对屏幕上留着的那一份。 */
export function applyRoomState(
  frame: RoomStateFrame,
  room: {
    turns: ReturnType<typeof useRoomTurns>
    runRecords: ReturnType<typeof useRunRecords>
    activity: ReturnType<typeof useRoomActivity>
    liveSteps: ReturnType<typeof useLiveSteps>
    typing: ReturnType<typeof useTypingPreview>
  }
) {
  room.turns.reconcile(frame.turn_ids, frame.since, frame.agents)
  for (const id of frame.turn_ids) room.runRecords.turnBegan(id)
  room.activity.snapshot(frame.members)
  room.liveSteps.keepOnly(new Set(Object.values(frame.agents ?? {})))
  room.typing.keepTurns(new Set(frame.turn_ids))
}
