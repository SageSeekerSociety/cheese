/** 上手引导那五步「这一步做了没有」的规则，以及「现在该说哪一步」。
 *
 *  这里只锁规则本身：判据从哪来、气泡画在哪、什么时候不问，都在各自的调用处
 *  （ChatPanel、StartGuide）。这套规则之所以单独拎出来，是因为清单卡和气泡要读
 *  同一份答案，两处各写一遍迟早会说岔。
 */
import type { StartFacts } from './startGuide'

import { describe, expect, it } from 'vitest'

import { CARD_STEPS, firstPendingStep, GUIDE_STEPS, stepDone } from './startGuide'

function facts(over: Partial<StartFacts> = {}): StartFacts {
  return {
    hasProject: false,
    agentHasSpoken: false,
    roomHasAttachment: false,
    libraryCount: null,
    forgeConnected: null,
    othersInProject: false,
    ...over,
  }
}

describe('上手引导的五步', () => {
  it('五步按顺序：建项目 → 说上话 → 放材料 → 接仓库 → 请同事', () => {
    expect(GUIDE_STEPS).toEqual(['project', 'talk', 'materials', 'repo', 'people'])
  })

  it('「开始清单」上没有建项目那一步——那张卡住在一个项目里，那时项目已经有了', () => {
    expect(CARD_STEPS).toEqual(['talk', 'materials', 'repo', 'people'])
  })
})

describe('每一步算不算做完', () => {
  it('建项目： rail 上有一个项目就算，没有就不算', () => {
    expect(stepDone('project', facts())).toBe(false)
    expect(stepDone('project', facts({ hasProject: true }))).toBe(true)
  })

  it('说上话：只看芝士在这个房间里开过口没有', () => {
    expect(stepDone('talk', facts())).toBe(false)
    expect(stepDone('talk', facts({ agentHasSpoken: true }))).toBe(true)
  })

  it('放材料：拖进房间算，放进项目资料库也算', () => {
    expect(stepDone('materials', facts())).toBe(false)
    expect(stepDone('materials', facts({ roomHasAttachment: true }))).toBe(true)
    expect(stepDone('materials', facts({ libraryCount: 2 }))).toBe(true)
  })

  it('资料库还没问到（null）不算做完，不能拿「不知道」当「没有」反过来用', () => {
    // 问不到时 useGettingStarted 会写 0，但那是它的事；这一层收到 null 就必须说
    // 「还没做」，否则气泡会跳到下一步而人其实什么都没放。
    expect(stepDone('materials', facts({ libraryCount: null }))).toBe(false)
    expect(stepDone('materials', facts({ libraryCount: 0 }))).toBe(false)
  })

  it('接仓库：只有明确接上了才算；还没问到（null）不算', () => {
    expect(stepDone('repo', facts())).toBe(false)
    expect(stepDone('repo', facts({ forgeConnected: true }))).toBe(true)
  })

  it('请同事：名册上有第二个人才算，只有自己不算', () => {
    expect(stepDone('people', facts())).toBe(false)
    expect(stepDone('people', facts({ othersInProject: true }))).toBe(true)
  })
})

describe('该说哪一步', () => {
  it('什么都没做时，从第一步开始', () => {
    expect(firstPendingStep(GUIDE_STEPS, facts())).toBe('project')
  })

  it('做完几步就往前走几步', () => {
    expect(firstPendingStep(GUIDE_STEPS, facts({ hasProject: true }))).toBe('talk')
    expect(firstPendingStep(GUIDE_STEPS, facts({ hasProject: true, agentHasSpoken: true }))).toBe('materials')
  })

  it('两件必做的做完，卡退场了，引导还继续走到接仓库、请同事', () => {
    const f = facts({ hasProject: true, agentHasSpoken: true, roomHasAttachment: true })
    expect(firstPendingStep(CARD_STEPS, f)).toBe('repo')
    expect(firstPendingStep(CARD_STEPS, facts({ ...f, forgeConnected: true }))).toBe('people')
  })

  it('五步都做完了就没有下一步了', () => {
    const done = facts({
      hasProject: true,
      agentHasSpoken: true,
      roomHasAttachment: true,
      forgeConnected: true,
      othersInProject: true,
    })
    expect(firstPendingStep(GUIDE_STEPS, done)).toBeNull()
    expect(firstPendingStep(CARD_STEPS, done)).toBeNull()
  })
})
