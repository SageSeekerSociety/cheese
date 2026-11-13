// 新手引导的后半程：第一步就是把第一件事交给芝士；动作步要人真的做到了才往下走；
// 界面上的地方用到时才讲；走完或跳过就不再自动出现；亲手采纳过交付的人不带；帮助菜单
// 能重新打开。
import { beforeEach, describe, expect, it } from 'vitest'

import {
  __resetProjectTourForTests,
  GUIDE_STEPS,
  noteFacts,
  replayTour,
  resolveGuideStep,
  useProjectTour,
} from './useProjectTour'

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

  it('moves past the reply once 芝士 has answered in the channel, and past the task once its page is open', () => {
    const guide = useProjectTour()
    const asked = { ...none, asked: true }
    noteFacts(asked)
    guide.stop.value = at('reply')
    guide.settle({ facts: asked, has: onPage('timeline') })
    expect(guide.current.value?.key).toBe('reply')
    const answered = { ...asked, answered: true }
    guide.settle({ facts: answered, has: onPage('timeline', 'agent-reply') })
    expect(guide.current.value?.key).toBe('confirm')
    guide.next()
    guide.settle({ facts: answered, has: onPage('task-page') })
    expect(guide.current.value?.key).toBe('taskPage')
  })

  it('waits on the reply while 芝士 is still working, even with an earlier reply of its on the page', () => {
    const guide = useProjectTour()
    const facts = { ...none, asked: true, answered: true }
    noteFacts(facts)
    guide.stop.value = at('reply')
    guide.settle({ facts, has: onPage('reply-coming', 'agent-reply') })
    expect(guide.current.value?.key).toBe('reply')
  })

  it('between the task page and accepting, waits for the preview and moves on once the delivery is in', () => {
    const guide = useProjectTour()
    const facts = { ...none, asked: true, answered: true }
    noteFacts(facts)
    guide.stop.value = at('preview')
    guide.settle({ facts, has: onPage('task-page', 'preview-card') })
    expect(guide.current.value?.key).toBe('preview')
    // 芝士没摆预览直接交付了，也不会卡在这一站。
    guide.settle({ facts, has: onPage('task-page', 'accept-card') })
    expect(guide.current.value?.key).toBe('accept')
    expect(at('preview')).toBe(at('taskPage') + 1)
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
      'agent-reply',
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

describe('导览这一站指哪里', () => {
  // 同一个名字永远是同一个元素：`toBe` 比的是它自己被指到没有。
  const els = new Map<string, HTMLElement>()
  const el = (name: string) => {
    if (!els.has(name)) els.set(name, { name } as unknown as HTMLElement)
    return els.get(name)!
  }
  const onPageEls =
    (...anchors: string[]) =>
    (name: string) =>
      anchors.includes(name) ? el(name) : null

  it('确认那一站：人已经在任务页上时，浮窗落在任务页上，不丢', () => {
    const stop = resolveGuideStep(GUIDE_STEPS[at('confirm')], onPageEls('task-page'))
    expect(stop?.target).toBe(el('task-page'))
  })

  it('到了任务页就是整块区域：不画圈，浮窗落在页面上', () => {
    const stop = resolveGuideStep(GUIDE_STEPS[at('taskPage')], onPageEls('task-page'))
    expect(stop?.target).toBe(el('task-page'))
    expect(stop?.text).toBe('text')
    expect(stop?.area).toBe(true)
  })

  it('预览一站圈芝士摆出来的那份；还没摆出来时落在任务页上等', () => {
    const shown = resolveGuideStep(GUIDE_STEPS[at('preview')], onPageEls('preview-card', 'task-page'))
    expect(shown?.target).toBe(el('preview-card'))
    expect(shown?.text).toBe('text')
    const waiting = resolveGuideStep(GUIDE_STEPS[at('preview')], onPageEls('task-page'))
    expect(waiting?.target).toBe(el('task-page'))
    expect(waiting?.text).toBe('textWaiting')
  })

  it('任务还没出现时指着任务卡等，不画圈', () => {
    const stop = resolveGuideStep(GUIDE_STEPS[at('taskPage')], onPageEls('task-row'))
    expect(stop?.target).toBe(el('task-row'))
    expect(stop?.text).toBe('textWaiting')
    expect(stop?.area).toBe(true)
  })

  it('芝士还在工作时，回复那一站指着工作那一行等，不说「回复了」', () => {
    const stop = resolveGuideStep(GUIDE_STEPS[at('reply')], onPageEls('reply-coming', 'agent-reply'))
    expect(stop?.target).toBe(el('reply-coming'))
    expect(stop?.text).toBe('textWaiting')
    const done = resolveGuideStep(GUIDE_STEPS[at('reply')], onPageEls('agent-reply'))
    expect(done?.target).toBe(el('agent-reply'))
    expect(done?.text).toBe('text')
  })

  it('一个能指的都没有时不出浮窗', () => {
    expect(resolveGuideStep(GUIDE_STEPS[at('taskPage')], () => null)).toBeNull()
  })

  it('重播时目标没开着就落到时间线上，而且每一站都能往下走', () => {
    const send = GUIDE_STEPS[at('send')]
    expect(resolveGuideStep(send, onPageEls('timeline'))).toBeNull()
    const stop = resolveGuideStep(send, onPageEls('timeline'), { replaying: true })
    expect(stop?.target).toBe(el('timeline'))
    expect(stop?.area).toBe(true)
    expect(stop?.action).toBe(false)
  })

  it('不是重播时，动作步做到了才算完，没有「下一步」', () => {
    const stop = resolveGuideStep(GUIDE_STEPS[at('send')], onPageEls('composer-send'))
    expect(stop?.action).toBe(true)
  })
})
