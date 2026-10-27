// 从看图浮层交给右侧面板的工具：只交给打开的那一张图，只用一次，过了时效不算。
import { afterEach, expect, it, vi } from 'vitest'

import { handOffImageTool, takeImageTool } from './chatImages'

afterEach(() => vi.useRealTimers())

it('hands the tool to the panel image that is that file, once', () => {
  handOffImageTool('uploads/a/poster.png', 'redact')
  expect(takeImageTool('["room","uploads/b/other.png"]')).toBeNull()
  expect(takeImageTool('["room","uploads/a/poster.png"]')).toBe('redact')
  expect(takeImageTool('["room","uploads/a/poster.png"]')).toBeNull()
})

it('forgets a hand-off the panel never picked up', () => {
  vi.useFakeTimers()
  handOffImageTool('uploads/a/poster.png', 'text')
  vi.advanceTimersByTime(60_000)
  expect(takeImageTool('["room","uploads/a/poster.png"]')).toBeNull()
})
