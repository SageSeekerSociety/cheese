/** 打开话题时分批挂行。
 *
 * 钉的是：只先挂最后一截、其余每让出一次补一批直到全部挂上；停在中间或要跳到某一条
 * 时一次挂完；人在补行时往上翻了，屏幕上的内容不动；要按 id 找某一条之前先全部挂上。
 */
import { nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BATCH_ROWS, INITIAL_ROWS, useRowBatch } from './useRowBatch'

function setup(rows: number, opts: { bottom?: boolean; restoresToBottom?: boolean } = {}) {
  const el = document.createElement('div')
  const onDone = vi.fn()
  const batch = useRowBatch({
    scrollRef: ref(el),
    atBottom: ref(opts.bottom ?? true),
    restoresToBottom: () => opts.restoresToBottom ?? true,
    rowCount: () => rows,
    onDone,
  })
  return { batch, onDone }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('首屏分批挂行', () => {
  it('先只挂最后一截，其余分批补齐，补完通知一次', async () => {
    const { batch, onDone } = setup(150)
    batch.startFor('t1', null)
    expect(batch.hidden.value).toBe(150 - INITIAL_ROWS)
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()
    expect(batch.hidden.value).toBe(150 - INITIAL_ROWS - BATCH_ROWS)
    await vi.advanceTimersByTimeAsync(1000)
    expect(batch.hidden.value).toBe(0)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('行数不多就不分批', () => {
    const { batch } = setup(INITIAL_ROWS)
    batch.startFor('t1', null)
    expect(batch.hidden.value).toBe(0)
  })

  it('要跳到某一条、或记下的位置不在底部：一次挂完', () => {
    const jump = setup(200).batch
    jump.startFor('t1', 'b9')
    expect(jump.hidden.value).toBe(0)
    const middle = setup(200, { restoresToBottom: false }).batch
    middle.startFor('t1', null)
    expect(middle.hidden.value).toBe(0)
  })

  it('人往上翻着时补行：上面长出来多少，scrollTop 就补多少', async () => {
    const el = document.createElement('div')
    const batch = useRowBatch({
      scrollRef: ref(el),
      atBottom: ref(false),
      restoresToBottom: () => true,
      rowCount: () => 150,
    })
    // 每挂上一行，内容高 20px。
    Object.defineProperty(el, 'scrollHeight', { get: () => (150 - batch.hidden.value) * 20 })
    batch.startFor('t1', null)
    el.scrollTop = 200
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()
    expect(batch.hidden.value).toBe(150 - INITIAL_ROWS - BATCH_ROWS)
    expect(el.scrollTop).toBe(200 + BATCH_ROWS * 20)
  })

  it('要按 id 找某一条：先全部挂上，渲染完再找', async () => {
    const { batch } = setup(150)
    batch.startFor('t1', null)
    const find = vi.fn()
    expect(batch.deferUntilRevealed(find)).toBe(true)
    expect(batch.hidden.value).toBe(0)
    expect(find).not.toHaveBeenCalled()
    await nextTick()
    expect(find).toHaveBeenCalledTimes(1)
    expect(batch.deferUntilRevealed(find)).toBe(false)
  })

  it('换话题（全部揭开）之后，上一轮还在路上的那一步不再动', async () => {
    const { batch, onDone } = setup(150)
    batch.startFor('t1', null)
    batch.revealAll()
    await vi.advanceTimersByTimeAsync(1000)
    expect(batch.hidden.value).toBe(0)
    expect(onDone).not.toHaveBeenCalled()
  })
})
