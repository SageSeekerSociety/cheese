/**
 * 读历史和订阅之间的那道缝。
 *
 * 房间的历史走 HTTP 读，之后落下的走 socket 推。读在订阅生效之前，而一条消息恰好落在
 * 两者之间，它两头都不在——安静的房间里后面没有新消息把它带出来，就一直缺着。订阅确认
 * 时服务端说了那一刻房间里最新的一条（`subscribed` 的 `newest`），比它新的都会推过来；
 * 手里（读回来的最新一页、之后推来的块）没有它，就是缝里掉了东西，补读一次。
 */
export function useRoomTail(options: {
  /** 此刻在哪个房间。 */
  roomId: () => string | undefined
  /** 正在读最新一页：读完再对，读回来的那一页可能正好带着它。 */
  reading: () => boolean
  /** 补读一次最新一页，不动 socket。 */
  catchUp: (roomId: string) => void
}) {
  let held = new Set<string>()
  let newest: string | null | undefined

  function check() {
    const roomId = options.roomId()
    if (newest === undefined || !roomId || options.reading()) return
    const wanted = newest
    newest = undefined
    if (wanted !== null && !held.has(wanted)) options.catchUp(roomId)
  }

  return {
    /** 换了房间：前一间的都不算。 */
    reset() {
      held = new Set()
      newest = undefined
    },
    /** 手里有了这一块（读回来的、推来的，不露面的也算）。 */
    hold(id: string) {
      held.add(id)
    },
    /** 订阅确认了，那一刻最新的是 `latest`（`undefined`：服务端没说）。 */
    subscribed(latest: string | null | undefined) {
      newest = latest
      check()
    },
    /** 一次读结束了：订阅确认时没对成的，现在对。 */
    check,
  }
}
