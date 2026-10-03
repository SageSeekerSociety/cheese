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
    timeline.show({ blocks: run(MAX_WINDOW, 'k'), hasMore: true })
    timeline.prepend([b('old1')], true)
    timeline.capNewest()

    expect(timeline.append(b('new')), '不插进眼前这一段').toBe('held')
    expect(ids(timeline.messages.value)).not.toContain('new')

    timeline.backToNewest()

    expect(timeline.hasNewer.value).toBe(false)
    expect(ids(timeline.messages.value)).toEqual([`k${MAX_WINDOW - 1}`, 'new'])
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
