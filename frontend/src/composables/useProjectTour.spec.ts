// 新手引导的后半程：第一步就是把第一件事交给芝士；动作步要人真的做到了才往下走；
// 界面上的地方用到时才讲；走完或跳过就不再自动出现；亲手采纳过交付的人不带；帮助菜单
// 能重新打开。
import { beforeEach, describe, expect, it } from 'vitest'

import { __resetProjectTourForTests, GUIDE_STEPS, noteFacts, replayTour, useProjectTour } from './useProjectTour'

const none = { asked: false, answered: false, accepted: false, learned: false }
const at = (key: string) => GUIDE_STEPS.findIndex((s) => s.key === key)

function onPage(...anchors: string[]) {
  const set = new Set(anchors)
  return (name: string) => set.has(name)
}

beforeEach(() => {
  localStorage.clear()
  __resetProjectTourForTests()
})

describe('新手引导的后半程', () => {
  it('waits for the facts before showing, and never shows to someone who has accepted a delivery', () => {
    const guide = useProjectTour()
    expect(guide.active.value).toBe(false)
    noteFacts({ ...none, learned: true })
    expect(guide.active.value).toBe(false)
    noteFacts(none)
    expect(guide.active.value).toBe(true)
  })

  it('starts at sending the first request, and stays there until a turn has run', () => {
    const guide = useProjectTour()
    noteFacts(none)
    const has = onPage('composer-input', 'page-workspace-overview', 'page-project-library', 'composer-send')
    expect(guide.current.value?.key).toBe('send')
    guide.settle({ facts: none, has })
    expect(guide.current.value?.key).toBe('send')
    guide.settle({ facts: { ...none, asked: true }, has })
    expect(guide.current.value?.key).toBe('reply')
  })

  it('moves past the reply once the thread is open, and past the task once its page is open', () => {
    const guide = useProjectTour()
    const facts = { ...none, asked: true }
    noteFacts(facts)
    guide.stop.value = at('reply')
    guide.settle({ facts, has: onPage('timeline') })
    expect(guide.current.value?.key).toBe('reply')
    guide.settle({ facts, has: onPage('reply-thread', 'thread-pane') })
    expect(guide.current.value?.key).toBe('confirm')
    guide.next()
    guide.settle({ facts, has: onPage('task-page') })
    expect(guide.current.value?.key).toBe('taskPage')
  })

  it('stays on the reply while 芝士 could not reply, even with the thread open', () => {
    const guide = useProjectTour()
    const facts = { ...none, asked: true }
    noteFacts(facts)
    guide.stop.value = at('reply')
    guide.settle({ facts, has: onPage('reply-failed', 'thread-pane') })
    expect(guide.current.value?.key).toBe('reply')
    guide.settle({ facts, has: onPage('reply-thread', 'thread-pane') })
    expect(guide.current.value?.key).toBe('confirm')
  })

  it('waits on the reply while 芝士 is still writing it, even with the thread open', () => {
    const guide = useProjectTour()
    const facts = { ...none, asked: true }
    noteFacts(facts)
    guide.stop.value = at('reply')
    guide.settle({ facts, has: onPage('reply-coming', 'thread-pane') })
    expect(guide.current.value?.key).toBe('reply')
  })

  it('once a delivery is accepted, shows where the results went, and is done after that', () => {
    const guide = useProjectTour()
    noteFacts(none)
    guide.stop.value = at('accept')
    const accepted = { ...none, asked: true, accepted: true }
    guide.settle({ facts: accepted, has: onPage('page-workspace-overview') })
    expect(guide.current.value?.key).toBe('overview')
    guide.next()
    expect(guide.done.value).toBe(true)
    expect(guide.active.value).toBe(false)
  })

  it('skips the overview stop where the channel page has no sidebar (a phone)', () => {
    const guide = useProjectTour()
    noteFacts(none)
    guide.stop.value = at('overview')
    guide.settle({ facts: { ...none, asked: true, accepted: true }, has: onPage('composer-input', 'composer-send') })
    expect(guide.done.value).toBe(true)
  })

  // 「我就想看看界面」：重播从第一站看起，不拿这个人以前发过、采纳过的记录往前跳。
  it('replaying walks from the first stop and ignores what the person did before', () => {
    const guide = useProjectTour()
    const didItAll = { asked: true, answered: true, accepted: true, learned: true }
    noteFacts(didItAll)
    replayTour()
    expect(guide.current.value?.key).toBe('send')

    const has = onPage(
      'composer-input',
      'composer-send',
      'timeline',
      'reply-thread',
      'thread-pane',
      'task-page',
      'page-workspace-overview'
    )
    guide.settle({ facts: didItAll, has })
    expect(guide.current.value?.key).toBe('send')

    guide.next()
    expect(guide.current.value?.key).toBe('reply')
  })

  it('stays done after skipping, and opens again from the help menu', () => {
    const guide = useProjectTour()
    noteFacts(none)
    guide.skip()
    __resetProjectTourForTests()
    expect(useProjectTour().done.value).toBe(true)
    replayTour()
    expect(guide.active.value).toBe(true)
    expect(guide.stop.value).toBe(0)
  })
})
