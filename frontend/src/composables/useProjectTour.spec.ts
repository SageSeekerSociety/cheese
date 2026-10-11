// 新手引导的后半程：介绍步点「下一步」往前；动作步要人真的做到了才往下走；走完或跳过
// 就不再自动出现；亲手采纳过交付的人不带；帮助菜单能重新打开。
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

  it('walks the introduction with next, then stops at sending until a turn has run', () => {
    const guide = useProjectTour()
    noteFacts(none)
    const has = onPage(
      'sidebar-general',
      'composer-input',
      'page-workspace-overview',
      'page-project-library',
      'composer-send'
    )
    for (let i = 0; i < at('send'); i += 1) guide.next()
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

  it('is done once a delivery is accepted', () => {
    const guide = useProjectTour()
    noteFacts(none)
    guide.stop.value = at('accept')
    guide.settle({ facts: { ...none, asked: true, accepted: true }, has: onPage() })
    expect(guide.done.value).toBe(true)
    expect(guide.active.value).toBe(false)
  })

  it('skips introduction stops whose target is not on the channel page (no sidebar on a phone)', () => {
    const guide = useProjectTour()
    noteFacts(none)
    guide.settle({ facts: none, has: onPage('composer-input', 'composer-send') })
    expect(guide.current.value?.key).toBe('composer')
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
