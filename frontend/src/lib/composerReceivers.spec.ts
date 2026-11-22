// 「这条会交给谁」这件事只有一个答案：后端按正文里点到的**每一位**队友各记一条投递
// （`delivery/mention.py` 的 `named_seats`），所以输入框说的接收者是全部，不是先收到
// 的那一位。这份钉的是那条规则落到输入框上的样子。
import { describe, expect, it } from 'vitest'

import { receiversOf } from './composerMentions'

type Mate = { handle: string; label: string }

const mateOf = (agent: { handle: string; name: string }): Mate => ({
  handle: agent.handle,
  label: agent.name,
})
const A = { handle: 'cheese-t1', label: '芝士' }
const B = { handle: 'cheese-t2', label: '芝士O' }

describe('这条会交给谁：听后端的', () => {
  it('后端说两位就列两位，按它给的先后', () => {
    expect(
      receiversOf(
        [
          { handle: A.handle, name: A.label },
          { handle: B.handle, name: B.label },
        ],
        [],
        mateOf
      )
    ).toEqual([A, B])
  })

  it('后端还答不上来时：先照正文里 @ 到的那几位，不然这一行会在打字中间空一下', () => {
    expect(receiversOf(undefined, [B], mateOf)).toEqual([B])
    expect(receiversOf(undefined, [A, B], mateOf)).toEqual([A, B])
  })

  it('后端答了空表（谁也不叫）：就是没有人，不拿正文猜回来', () => {
    expect(receiversOf([], [A], mateOf)).toEqual([])
  })
})
