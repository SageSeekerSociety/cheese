/** 预览里「问个人芝士」那一项：有没有、按下去做什么。 */
import { expect, it, vi } from 'vitest'

import { personalAsker } from './personalAsker'

it('没有个人芝士时不给这一条：面板据此不列那一项', () => {
  expect(personalAsker(false, vi.fn(), vi.fn())).toBeUndefined()
})

it('有的时候按下去开面板、把那句话问过去', () => {
  const open = vi.fn()
  const ask = vi.fn()
  const handler = personalAsker(true, open, ask)
  expect(handler).toBeTypeOf('function')

  handler!('这一处的数字不对')
  // 先开面板再看那句话：面板不开，问了也看不见。
  expect(open).toHaveBeenCalledTimes(1)
  expect(ask).toHaveBeenCalledWith('这一处的数字不对')
})
