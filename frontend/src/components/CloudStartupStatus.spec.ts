import type { Block } from '../cx_types'

import { fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { collapseNotices } from '../lib/platformNotice'

import CloudStartupStatus from './CloudStartupStatus.vue'

function event(id: string, seconds: number, content: string, state?: string): Block {
  return {
    id,
    topic_id: 'room',
    kind: 'event',
    author: 'system',
    author_type: 'platform',
    content,
    created_at: new Date(Date.UTC(2026, 8, 21, 12, 0, seconds)).toISOString(),
    meta: { event_type: state ? 'cloud_provisioning' : 'cloud_startup', ...(state ? { state } : {}) },
  }
}

afterEach(() => vi.useRealTimers())

it('shows live elapsed time, retains logs on completion, and stops the clock', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-21T12:00:30Z'))
  const events = [event('1', 0, '创建机器', 'waiting'), event('2', 10, '正在传输运行程序')]
  const view = render(CloudStartupStatus, { props: { events } })
  expect(view.getByText(/正在传输运行程序 · 已等待 30秒/)).toBeTruthy()
  await fireEvent.click(view.container.querySelector('summary')!)
  expect(view.getByRole('list', { name: '启动日志' })).toBeTruthy()
  await vi.advanceTimersByTimeAsync(41000)
  expect(view.getByText(/没有新的启动进度/)).toBeTruthy()
  await view.rerender({ events: [...events, event('3', 80, '已接入，继续处理消息', 'ready')] })
  expect(view.getByText(/运行环境已就绪 · 共用时 1分20秒/)).toBeTruthy()
  expect(view.getByText('正在传输运行程序')).toBeTruthy()
  await vi.advanceTimersByTimeAsync(60000)
  expect(view.getByText(/共用时 1分20秒/)).toBeTruthy()
  view.unmount()
})

it('keeps startup in one message when the user speaks while waiting', () => {
  const waiting = event('1', 0, '等待', 'waiting')
  const message = {
    ...event('2', 5, '还没好吗'),
    kind: 'message' as const,
    author: 'user',
    author_type: 'participant' as const,
    meta: null,
  }
  const rows = collapseNotices([waiting, message, event('3', 10, '安装工具'), event('4', 20, '就绪', 'ready')])
  expect(rows).toHaveLength(2)
  expect(rows[0].run.map((item) => item.id)).toEqual(['1', '3', '4'])
})
