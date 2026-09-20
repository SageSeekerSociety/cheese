// 「这条是谁说的」——分栏靠右还是靠左、按 Markdown 渲染还是逐字渲染、画哪个头像，
// 三件事都问这里。所以这一份把两个判据拆开喂：**档位**（participant / system，以及
// 存量行上的 human / ai）和**署名**（agent handle / 人的 handle）。
//
// 拆开喂是这份用例存在的理由：一条 participant 的消息，署名是人还是芝士，答案必须
// 相反；而一条 system 的事件不论署名是什么，两个都是 false——它不是谁说的话。只喂
// 旧值的用例断言不了这件事，因为人和 agent 在旧值上本来就分在两档里，判据错了也照
// 样绿。

import type { AuthorType, Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { isAgentBlock, isAgentHandle, isPersonBlock } from './authorship'

function block(author: string, author_type: AuthorType): Pick<Block, 'author_type' | 'author'> {
  return { author, author_type }
}

describe('这个 handle 是不是芝士的', () => {
  it('平台身份和每一个实例都是', () => {
    expect(isAgentHandle('cheese')).toBe(true)
    expect(isAgentHandle('cheese-7f3a')).toBe(true)
  })

  it('人不是，哪怕名字里带着 cheese', () => {
    expect(isAgentHandle('bobby')).toBe(false)
    expect(isAgentHandle('cheeseburger-fan')).toBe(false)
    // 前缀是 `cheese-`，不是 `cheese`：少了那一横就是另一个人的 handle。
    expect(isAgentHandle('cheesemonger')).toBe(false)
  })
})

describe('participant：是谁说的，只由署名回答', () => {
  it('署名是芝士的，是芝士说的', () => {
    expect(isAgentBlock(block('cheese-7f3a', 'participant'))).toBe(true)
    expect(isPersonBlock(block('cheese-7f3a', 'participant'))).toBe(false)
  })

  it('署名是人的，是人说的', () => {
    // 负向对照：档位和上面一条一模一样，只有署名不同，两个答案必须整个反过来。
    // 判错的话，符露夀在房间里说的每一句都会按 Markdown 渲染、跑到左边去。
    expect(isAgentBlock(block('bobby', 'participant'))).toBe(false)
    expect(isPersonBlock(block('bobby', 'participant'))).toBe(true)
  })
})

describe('platform：不是谁说的话', () => {
  it('两个都是 false，不论署名', () => {
    // 平台事件顶着谁的 handle 是常事（「<@bobby> 合并了 #12」），所以这一档必须先
    // 于署名判：漏掉它，一条部署提醒会被当成 bobby 说的话，带上头像挂到时间线上。
    expect(isAgentBlock(block('cheese', 'system'))).toBe(false)
    expect(isPersonBlock(block('cheese', 'system'))).toBe(false)
    expect(isAgentBlock(block('bobby', 'system'))).toBe(false)
    expect(isPersonBlock(block('bobby', 'system'))).toBe(false)
  })
})

describe('存量行的两个旧值：一样是参与者', () => {
  it('ai / human 都按署名回答，和 participant 同一个答案', () => {
    // 这两档只出现在 P8 之前写下的行上，而房间历史只增不减：滚回去看到的仍然是
    // 它们，渲染必须一字不差地照旧。
    expect(isAgentBlock(block('cheese', 'ai'))).toBe(true)
    expect(isPersonBlock(block('cheese', 'ai'))).toBe(false)
    expect(isAgentBlock(block('bobby', 'human'))).toBe(false)
    expect(isPersonBlock(block('bobby', 'human'))).toBe(true)
  })
})
