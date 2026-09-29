// 从历史中间打开的一段，往下翻到头要和最新的那一段合成一条完整的时间线：不缺、不重，
// 停在中间那阵子里改过、撤回的也都算数。
import type { Block } from '../../../cx_types'

import { describe, expect, it } from 'vitest'

import { useTimeline } from './useTimeline'

const b = (id: string, content = id): Block => ({ id, content }) as Block
const ids = (blocks: Block[]) => blocks.map((x) => x.id)

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
