import { expect, it, vi } from 'vitest'

import {
  clearAnnotationGuard,
  confirmAnnotationDiscard,
  hasUnsentAnnotations,
  setAnnotationGuard,
} from './annotationDiscard'

it('没人在标注时直接放行，不弹任何东西', async () => {
  expect(hasUnsentAnnotations()).toBe(false)
  await expect(confirmAnnotationDiscard()).resolves.toBe(true)
})

it('有人登记了就把「要不要丢」交给它答', async () => {
  const guard = { confirmDiscard: vi.fn().mockResolvedValue(false) }
  setAnnotationGuard(guard)
  expect(hasUnsentAnnotations()).toBe(true)
  await expect(confirmAnnotationDiscard()).resolves.toBe(false)
  expect(guard.confirmDiscard).toHaveBeenCalledTimes(1)
  clearAnnotationGuard(guard)
})

it('只清自己那一份：别人（晚登记的）登记的不会被误撤', () => {
  const mine = { confirmDiscard: vi.fn() }
  const theirs = { confirmDiscard: vi.fn() }
  setAnnotationGuard(mine)
  setAnnotationGuard(theirs)
  clearAnnotationGuard(mine)
  // 后登记的盖过先登记的：撤 mine 不该把 guard 清空。
  expect(hasUnsentAnnotations()).toBe(true)
  clearAnnotationGuard(theirs)
  expect(hasUnsentAnnotations()).toBe(false)
})
