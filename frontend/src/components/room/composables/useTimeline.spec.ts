// 从历史中间打开的一段，往下翻到头要和最新的那一段合成一条完整的时间线：不缺、不重，
// 停在中间那阵子里改过、撤回的也都算数。
import type { Block } from '../../../cx_types'

import { describe, expect, it } from 'vitest'

import { MAX_WINDOW } from '../../../lib/blockPaging'

import { useTimeline } from './useTimeline'

const b = (id: string, content = id): Block => ({ id, content }) as Block
const ids = (blocks: Block[]) => blocks.map((x) => x.id)
/** n 条 m0..m{n-1}，名字带前缀好分辨是哪一段。 */
const run = (n: number, prefix = 'm') => Array.from({ length: n }, (_, i) => b(`${prefix}${i}`))
/** 带 `created_at` 的一条：`append` 现在按时间落位（`placeBlock`），有时间的用例得给真时间。 */
const timed = (id: string, ms: number): Block => ({ id, created_at: new Date(ms).toISOString() }) as Block
/** n 条带时间的 m0..m{n-1}，第 i 条比前一条晚一秒。 */
const timedRun = (n: number, prefix = 'm') => Array.from({ length: n }, (_, i) => timed(`${prefix}${i}`, i * 1000))

describe('停在历史中间的一段', () => {
  it('往下翻到和最新的一段接上，就是一条完整的时间线', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: [b('n1'), b('n2'), b('n3')], hasMore: true })
    timeline.showMiddle({ blocks: [b('o1'), b('o2')], hasMore: true }, false)
    expect(timeline.hasNewer.value).toBe(true)

    timeline.appendNewer([b('o3'), b('n1')], false)

    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value)).toEqual(['o1', 'o2', 'o3', 'n1', 'n2', 'n3'])
  })

  it('停在中间那阵子里来的、改过的、撤回的，回到最新时都在', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: [b('n1'), b('n2')], hasMore: true })
    timeline.showMiddle({ blocks: [b('o1')], hasMore: true }, false)

    expect(timeline.append(b('n3'))).toBe('held')
    timeline.replace(b('n1', 'edited'))
    timeline.remove('n2')
    expect(ids(timeline.messages.value)).toEqual(['o1'])

    timeline.backToNewest()
    expect(ids(timeline.messages.value)).toEqual(['n1', 'n3'])
    expect(timeline.messages.value[0].content).toBe('edited')
  })

  it('取回来的那一段本来就挨着最新的：直接是平常的样子', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: [b('n1'), b('n2')], hasMore: true })
    timeline.showMiddle({ blocks: [b('o9'), b('n1')], hasMore: true }, false)
    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value)).toEqual(['o9', 'n1', 'n2'])
  })
})

// 往上翻没有尽头：一屏一屏补上去，窗口会一直涨。`capNewest` 把它收在上限内，裁掉
// 的是**最新**那一截（读的人在上面，不能动的是他眼前那些），并把这一截挪到背后——
// 新消息、回到最新、往下接都还认它。
describe('窗口涨过上限', () => {
  it('没到上限就什么都不做', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: run(10), hasMore: true })

    timeline.capNewest()

    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value)).toHaveLength(10)
  })

  it('裁掉最新的一截：留下的还是最老的那些', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: run(MAX_WINDOW, 'k'), hasMore: true })
    timeline.prepend([b('old1')], true)

    timeline.capNewest()

    expect(ids(timeline.messages.value)).toHaveLength(MAX_WINDOW)
    expect(timeline.messages.value[0].id).toBe('old1')
    // 最早的一截顶上不动，最新那一条（k599）被裁下去了。
    expect(ids(timeline.messages.value).at(-1)).toBe(`k${MAX_WINDOW - 2}`)
    expect(timeline.hasNewer.value).toBe(true)
  })

  it('裁下来的那一截收在背后：新消息落在它那儿，回到最新一起回来', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: timedRun(MAX_WINDOW, 'k'), hasMore: true })
    timeline.prepend([b('old1')], true)
    timeline.capNewest()

    // 一条比 k599 还新（`append` 按 created_at 落位）：落进背后那一段，不插进眼前。
    expect(timeline.append(timed('new', MAX_WINDOW * 1000)), '不插进眼前这一段').toBe('held')
    expect(ids(timeline.messages.value)).not.toContain('new')

    timeline.backToNewest()

    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value)).toEqual([`k${MAX_WINDOW - 1}`, 'new'])
  })

  it('比背后那一段还早的回放帧留给往上翻的页，不插进去', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: timedRun(MAX_WINDOW, 'k'), hasMore: true })
    timeline.prepend([b('old1')], true)
    timeline.capNewest()

    // 老于背后那段的第一块（k599），而上面还有没取到的历史：`placeBlock` 认不出该
    // 把它放哪，交还给往上翻的那一页，别硬塞进最新的那一段里。
    expect(timeline.append(timed('replayed', 0))).toBe('above')
    expect(ids(timeline.newest().blocks)).toEqual([`k${MAX_WINDOW - 1}`])
  })

  it('往下翻时接回来，两段合成一条', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: run(MAX_WINDOW, 'k'), hasMore: true })
    timeline.prepend([b('old1')], true)
    timeline.capNewest()

    // 显示段停在 k598，背后是 k599；after 游标把它取回来。
    timeline.appendNewer([b(`k${MAX_WINDOW - 1}`)], true)

    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value).slice(-3)).toEqual([
      `k${MAX_WINDOW - 3}`,
      `k${MAX_WINDOW - 2}`,
      `k${MAX_WINDOW - 1}`,
    ])
  })

  it('停在中间时裁掉的那一截直接丢，背后那段不被搅乱', () => {
    const timeline = useTimeline()
    timeline.show({ blocks: run(3, 'n'), hasMore: true })
    timeline.showMiddle({ blocks: run(MAX_WINDOW, 'o'), hasMore: true }, false)
    expect(timeline.hasNewer.value).toBe(true)

    timeline.prepend([b('older')], true)
    timeline.capNewest()

    // 显示段被裁，但背后还是打开时那段最新的 n0..n2。
    expect(ids(timeline.newest().blocks)).toEqual(['n0', 'n1', 'n2'])
  })
})

// 房间里事件常比消息多：一串不露面的块（`in_room:false` 的事件、前端错误、不在白名单
// 里的块）画不出任何一行，却和消息一样占窗口额度。`renders` 把画不出来的挡在窗口外，
// 上限数的就是画得出来的那些，封顶裁下的「最新的一截」也不会再是一屏空的。
describe('不露面的块不占窗口', () => {
  const inRoom = (x: Block) => (x as { meta?: { in_room?: boolean } }).meta?.in_room !== false
  const hidden = (id: string): Block => ({ id, meta: { in_room: false } }) as unknown as Block

  it('不露面的块根本不进窗口', () => {
    const timeline = useTimeline({ renders: inRoom })
    timeline.show({ blocks: [b('v0'), hidden('h0'), hidden('h1'), b('v1')], hasMore: true })

    expect(ids(timeline.messages.value)).toEqual(['v0', 'v1'])
  })

  it('实时推来的不露面事件不收进窗口，也不落进背后那一段', () => {
    const timeline = useTimeline({ renders: inRoom })
    timeline.show({ blocks: [b('v0')], hasMore: true })

    expect(timeline.append(hidden('h0'))).toBe('known')
    expect(ids(timeline.messages.value)).toEqual(['v0'])
  })

  it('回到最新落回最新那条看得见的，不被一串不露面的事件挤掉', () => {
    // 最新那条看得见的消息（v0）后面跟着一页不露面的事件。
    const timeline = useTimeline({ renders: inRoom })
    timeline.show({ blocks: [b('v0'), ...run(50, 'h').map((x) => hidden(x.id))], hasMore: true })

    // 往上翻：可见的历史一页页补到顶上。差这么一点，使裁下的「最新的一截」正好是
    // 不露面那 50 条——不挡的话，v0 会落在留下的一截里，被挪到背后那截顶掉。
    timeline.prepend(run(599, 'k'), true)
    timeline.capNewest()

    timeline.backToNewest()

    // 回来时最新那条看得见的（v0）必须还在，且排在末位。
    expect(ids(timeline.messages.value)).toContain('v0')
    expect(ids(timeline.messages.value).at(-1)).toBe('v0')
  })

  it('整页都不露面时，游标仍指向读过的最老那条（窗口里没有可见的也跟着走）', () => {
    const timeline = useTimeline({ renders: inRoom })
    timeline.show({ blocks: [hidden('h0'), hidden('h1')], hasMore: true })

    expect(ids(timeline.messages.value), '一条都画不出来').toEqual([])
    expect(timeline.oldestLoaded(), '游标是读到哪了，不是画得出什么').toBe('h0')

    timeline.prepend([hidden('x0'), b('v0')], true)

    expect(ids(timeline.messages.value)).toEqual(['v0'])
    expect(timeline.oldestLoaded(), '翻过一页，游标跟着往前挪').toBe('x0')
  })

  it('prepend 回「这一页多画出来了几行」：整页不露面回 0，接上可见的才 >0', () => {
    // 翻页的人据此知道该不该接着往回读：整页一行都画不出来（回 0）时，这一页没让任何
    // 东西长高，就没有下一次滚动事件，翻页会停在原地。
    const timeline = useTimeline({ renders: inRoom })
    timeline.show({ blocks: [b('v0')], hasMore: true })

    expect(
      timeline.prepend(
        run(50, 'h').map((x) => hidden(x.id)),
        true
      ),
      '整页不露面'
    ).toBe(0)
    expect(timeline.prepend([hidden('j0'), b('j1')], true), '接上一条看得见的').toBe(1)
    expect(timeline.prepend([b('j1')], true), '已经有的不算多出来').toBe(0)
  })
})
