/**
 * 房间读一次最新一页历史期间的记账：读的这段时间里 socket 推来的变化（新块、改动、
 * 撤回、表情）先记下，读回来之后补上（lib/blockPaging 的 applyLiveChanges），这一页
 * 才盖不掉比它新的东西。同一时刻只认最新的那一次读：换了房间、又发起了一次，前一次
 * 回来就作废。
 *
 * 进房间（loadTopic）和断线重连（composables/useRoomResync）都从 `begin` 进。进房间
 * 那一次读完之前断了线，重连要重新进一次，不能只做重连的那一半：骨架屏、未读线、
 * 定位到点名的那条都是进房间那一次才做的（`entered`）。
 */

import type { Block, ReactionAgg } from '../../../cx_types'

export function useHistoryReads(options: {
  /** 此刻在哪个房间。 */
  roomId: () => string | undefined
  /** 面板已经卸载：还在飞的请求回来时什么都不该写。 */
  disposed: () => boolean
}) {
  let changes: Map<string, Block | null> | null = null
  let reactions: Map<string, ReactionAgg[]> | null = null
  let generation = 0
  let enteredRoom: string | null = null

  /** `entering`：这是进房间的那一次读，读完（`done`）之前这间房不算进来了。 */
  function begin(roomId: string, entering = false) {
    const mine = ++generation
    if (entering) enteredRoom = null
    const read = {
      changes: (changes = new Map<string, Block | null>()),
      reactions: (reactions = new Map<string, ReactionAgg[]>()),
      /** 这一次还算数：面板在、没有更新的一次、还在这个房间。 */
      stillHere: () => !options.disposed() && mine === generation && options.roomId() === roomId,
      /** 进房间的那一次读成功了。 */
      done: () => {
        if (read.stillHere()) enteredRoom = roomId
      },
      /** 读完了：还是最新的那一次就收起记账，答 true。 */
      end: () => {
        if (mine !== generation) return false
        changes = reactions = null
        return true
      },
    }
    return read
  }

  return {
    begin,
    /** 进这间房的那一次读已经成功：之后断线只需重连，不用重新进。 */
    entered: (roomId: string) => enteredRoom === roomId,
    /** 正在读：socket 推来的块这时不演「新到」，读回来那一页会把它放到位。 */
    reading: () => changes !== null,
    /** 一块到了或变了（`null`：撤回了）。 */
    note: (id: string, block: Block | null) => changes?.set(id, block),
    /** 读的这段时间里改过的表情，读回来之后补上（useMessageReactions 写）。 */
    pendingReactions: () => reactions,
  }
}
