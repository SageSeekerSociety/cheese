import type { Block } from '../../../cx_types'

/**
 * 房间的页面读得到的那些块：和后端 `indexed_rows.SHOWN_ROWS` 同一条（`?shown=true`），
 * `newest` 也只数它们。芝士干活时一步一步推来的块不露面，算进手里最大的号会把它撑过
 * `newest`，正好盖住缝里掉的那一条。
 */
const SHOWN_KINDS = new Set(['message', 'attachment', 'artifact', 'event'])
function shown(block: Block): boolean {
  return SHOWN_KINDS.has(block.kind) && (block.meta as { in_room?: boolean } | null | undefined)?.in_room !== false
}

/**
 * 读历史和订阅之间的那道缝。
 *
 * 房间的历史走 HTTP 读，之后落下的走 socket 推。读在订阅生效之前，而一条消息恰好落在
 * 两者之间，它两头都不在——安静的房间里后面没有新消息把它带出来，就一直缺着。订阅确认
 * 时服务端说了那一刻房间里最后存进来的那一条的编号（`subscribed` 的 `newest`，见
 * `Block.seq`），比它晚的都会推过来；手里（读回来的、之后推来的块）最大的编号比它小，
 * 就是缝里掉了东西，把那个号之后存进来的补读一次。
 *
 * 按编号而不按时间：芝士的一条消息记的是它开始写的时刻，写完才存，会排在已经存好的
 * 几条前面。按时间问「最新那条之后的」永远问不到它。
 */
export function useRoomTail(options: {
  /** 此刻在哪个房间。 */
  roomId: () => string | undefined
  /** 正在读最新一页：读完再对，读回来的那一页可能正好带着它。 */
  reading: () => boolean
  /** 补读编号在 `after` 之后存进来的，不动 socket。 */
  catchUp: (roomId: string, after: number) => void
}) {
  /** 手里最大的编号；0 是还什么都没有。 */
  let held = 0
  let newest: number | null | undefined

  function check() {
    const roomId = options.roomId()
    if (newest === undefined || !roomId || options.reading()) return
    const wanted = newest
    newest = undefined
    if (wanted !== null && wanted > held) options.catchUp(roomId, held)
  }

  return {
    /** 换了房间：前一间的都不算。 */
    reset() {
      held = 0
      newest = undefined
    },
    /** 手里有了这一块（读回来的、推来的）；房间的页面读不到的不算。 */
    hold(block: Block) {
      if (block.seq !== undefined && block.seq > held && shown(block)) held = block.seq
    },
    /** 订阅确认了，那一刻最后存进来的是 `latest` 号（`undefined`：服务端没说）。 */
    subscribed(latest: number | null | undefined) {
      newest = latest
      check()
    },
    /** 一次读结束了：订阅确认时没对成的，现在对。 */
    check,
  }
}
