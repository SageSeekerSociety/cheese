/** 已读的几段编号：并起来不丢不重；一条消息谁读了只看编号落没落在他读过的那几段里，
 * 说话的人自己不算。 */
import type { ReceiptPerson } from './readReceipts'

import { describe, expect, it } from 'vitest'

import { mergeStretches, receiptOf, stretchesOf } from './readReceipts'

const person = (handle: string): ReceiptPerson => ({ handle, name: handle, avatar: null, isAgent: false })

describe('readReceipts', () => {
  it('joins overlapping and touching stretches, keeps a real gap', () => {
    expect(
      mergeStretches([
        [8, 9],
        [1, 3],
        [4, 5],
        [2, 4],
      ])
    ).toEqual([
      [1, 5],
      [8, 9],
    ])
  })

  it('turns scattered numbers into the runs a report carries', () => {
    expect(stretchesOf([5, 3, 4, 9])).toEqual([
      [3, 5],
      [9, 9],
    ])
  })

  it('splits readers from non-readers by the message number, leaving out its author', () => {
    const reads = new Map<string, [number, number][]>([
      ['zhou', [[1, 4]]],
      ['lin', [[6, 9]]],
    ])
    const receipt = receiptOf(5, 'me', [person('me'), person('zhou'), person('lin')], reads)
    expect(receipt).toEqual({ read: [], unread: [person('zhou'), person('lin')] })
    expect(receiptOf(4, 'me', [person('me'), person('zhou'), person('lin')], reads)).toEqual({
      read: [person('zhou')],
      unread: [person('lin')],
    })
  })

  it('has nothing to show when nobody but the author can read it', () => {
    expect(receiptOf(1, 'me', [person('me')], new Map())).toBeNull()
  })
})
