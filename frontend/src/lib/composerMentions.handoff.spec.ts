// 「交给芝士」（仅你可见那一句的按钮）写进正文的那个 @，和那一句上写着的名字必须是同一位。
//
// 报上来的那条：一个频道里既有芝士、又有芝士O。先在回复里 @ 过芝士O，再说一句谁也没 @ 的
// 话，那句「仅你可见」出现时上面写着「交给芝士」，点完正文里却是 `@芝士O`——之前这条用例
// 钉的只有草稿为空的一半。草稿里已经 @ 着人时，以前整段不动，于是同一处里还留着那一次
// 留下的另一位。
//
// 这里钉的是那条规则本身：换人时换的是**开头那几位叫人的 @**，正文里提到的队友是在说事，
// 开头 @ 的人是在对他说，两种都不动。
import { describe, expect, it } from 'vitest'

import { handToMentions } from './composerMentions'

const SEAT = { handle: 'cheese-t1', label: '芝士' }
const OTHER = { handle: 'cheese-t2', label: '芝士O' }
const POOL = [
  { handle: 'alice', label: 'Alice' },
  { handle: OTHER.handle, label: OTHER.label, agent: true },
]
const MATES = [SEAT, OTHER]
const TOPICS: { id: string; title: string }[] = []

const MEMBERS = [...MATES, ...POOL]
const hand = (text: string) => handToMentions(text, SEAT, MATES, MEMBERS, TOPICS, true)
const handAnon = (text: string) => handToMentions(text, SEAT, MATES, MEMBERS, TOPICS, false)

describe('交给芝士：写进去的 @ 就是那一句上写着的那位', () => {
  it('空草稿：写开头一个 @', () => {
    expect(hand('正文')).toBe(`<@${SEAT.handle}> 正文`)
  })

  it('草稿里 @ 着另一位队友：换成这一位，不是原样不动', () => {
    expect(hand(`<@${OTHER.handle}> 正文`)).toBe(`<@${SEAT.handle}> 正文`)
  })

  it('草稿里开头 @ 着好几位：都换成这一位，不留下原来那几位', () => {
    expect(hand(`<@${OTHER.handle}> <@${SEAT.handle}> 正文`)).toBe(`<@${SEAT.handle}> 正文`)
  })

  it('草稿里 @ 着的就是这一位：不动，不多写一个', () => {
    expect(hand(`<@${SEAT.handle}> 正文`)).toBe(`<@${SEAT.handle}> 正文`)
  })

  it('只换开头那几位：正文里提到的队友原样留着', () => {
    expect(hand(`<@${OTHER.handle}> 照 <@${OTHER.handle}> 说的改一下`)).toBe(
      `<@${SEAT.handle}> 照 <@${OTHER.handle}> 说的改一下`
    )
  })

  it('开头 @ 的是人：那是说给他听的，不拿掉，@ 加在人后面', () => {
    expect(hand('<@alice> 你看一下')).toBe(`<@${SEAT.handle}> <@alice> 你看一下`)
  })

  // 开头的空白、或者开头先 @ 了个人，都不能挡住后面那位队友——它同样是这条交给的人。
  it('开头夹着空行时照样换掉那位队友', () => {
    expect(hand(`\n<@${OTHER.handle}> 正文`)).toBe(`<@${SEAT.handle}> 正文`)
  })

  it('开头先 @ 了人、后面跟着队友：人留着，队友换成这一位', () => {
    expect(hand(`<@alice> <@${OTHER.handle}> 正文`)).toBe(`<@${SEAT.handle}> <@alice> 正文`)
  })

  // 只在开头那一段上动手：句子后面打的字原样交回，不会被顺手展开成标签。
  it('句子后面的 @ 和结尾的换行原样留着', () => {
    expect(hand(`<@${OTHER.handle}> 照 @alice 说的改\n`)).toBe(`<@${SEAT.handle}> 照 @alice 说的改\n`)
  })

  it('没人指定是谁（头像的老调用）时，草稿里已有的队友不动', () => {
    expect(handAnon(`<@${OTHER.handle}> 正文`)).toBe(`<@${OTHER.handle}> 正文`)
    expect(handAnon('正文')).toBe(`<@${SEAT.handle}> 正文`)
  })
})
