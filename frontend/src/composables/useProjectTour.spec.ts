// 界面导览走到哪、看过没有：走完或跳过就不再自动出现；帮助菜单能重新打开；「第一次」
// 提示各出现一次。
import { beforeEach, describe, expect, it } from 'vitest'

import { __resetProjectTourForTests, replayTour, useProjectTour } from './useProjectTour'

beforeEach(() => {
  localStorage.clear()
  __resetProjectTourForTests()
})

describe('界面导览', () => {
  it('walks the stops in order and is done after the last', () => {
    const tour = useProjectTour()
    expect(tour.tourDone.value).toBe(false)
    tour.next(3)
    tour.next(3)
    expect(tour.stop.value).toBe(2)
    tour.back()
    expect(tour.stop.value).toBe(1)
    tour.next(3)
    tour.next(3)
    expect(tour.tourDone.value).toBe(true)
  })

  it('stays done across a reload once skipped', () => {
    useProjectTour().skip()
    __resetProjectTourForTests()
    expect(useProjectTour().tourDone.value).toBe(true)
  })

  it('opens again from the help menu, from the first stop', () => {
    const tour = useProjectTour()
    tour.next(5)
    tour.skip()
    replayTour()
    expect(tour.tourDone.value).toBe(false)
    expect(tour.replaying.value).toBe(true)
    expect(tour.stop.value).toBe(0)
  })

  it('shows each first-time tip once', () => {
    const tour = useProjectTour()
    expect(tour.tipSeen('task')).toBe(false)
    tour.dismissTip('task')
    __resetProjectTourForTests()
    expect(useProjectTour().tipSeen('task')).toBe(true)
    expect(useProjectTour().tipSeen('accept')).toBe(false)
  })
})
