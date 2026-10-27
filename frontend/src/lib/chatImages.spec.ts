// 从看图浮层交给右侧面板的工具：只交给打开的那一张图，只用一次，过了时效不算。
import { afterEach, expect, it, vi } from 'vitest'

import { handOffImageTool, imagePaths, takeImageTool } from './chatImages'

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

it('does not let a shorter path claim another picture that contains it', () => {
  // `a/b.png` 是 `uploads/x/a/b.png` 的一段后缀：按子串认，字和打码会落到那张图上。
  handOffImageTool('a/b.png', 'text')
  expect(takeImageTool('["room","uploads/x/a/b.png"]')).toBeNull()
})

it('collects the conversation’s pictures in order, each once, folded runs included', () => {
  const rows = [
    { block: { kind: 'message', content: 'hi' }, run: [] },
    {
      block: { kind: 'attachment', mime_type: 'image/png', content: 'up/a.png' },
      run: [
        { kind: 'attachment', mime_type: 'image/png', content: 'up/a.png' },
        { kind: 'attachment', mime_type: 'image/jpeg', content: 'up/b.jpg' },
      ],
    },
    { block: { kind: 'attachment', mime_type: 'image/png', content: 'up/a.png' }, run: [] },
    { block: { kind: 'attachment', mime_type: 'application/pdf', content: 'up/c.pdf' }, run: [] },
    { block: { kind: 'artifact', mime_type: 'image/png', content: 'up/d.png' }, run: [] },
  ]
  expect(imagePaths(rows)).toEqual(['up/a.png', 'up/b.jpg'])
})
