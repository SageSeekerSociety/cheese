// 现场顶上那一行：只要一轮在跑就说点什么——在想、在做哪一步、在重试、在等机器——
// 并说这一轮跑了多久、多久没动静了。
import type { Block } from '../../cx_types'

import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SiteStatusBar from './SiteStatusBar.vue'

import { setLocale } from '@/i18n'

const NOW = Date.parse('2026-09-25T10:05:00Z')

function row(id: string, at: string, meta: Record<string, unknown>, turn: string | null = 'turn-a'): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-t1',
    content: '',
    reply_to: null,
    refs: [],
    turn_id: turn,
    meta,
    created_at: at,
  } as unknown as Block
}

function bar(blocks: Block[], working: boolean, turns: Record<string, number> = {}) {
  const { container } = render(SiteStatusBar, { props: { blocks, working, turns } })
  return container.querySelector('[data-testid="site-status"]')?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
}

const RUNNING = { 'turn-a': Date.parse('2026-09-25T10:03:00Z') }

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('现场状态条', () => {
  it('一轮刚开始、还一行都没有：思考中，并说这一轮跑了多久', () => {
    const text = bar([], true, RUNNING)
    expect(text).toContain('思考中')
    expect(text).toContain('已用 2 分 00 秒')
  })

  it('最新的一行是刚开始的一步：说正在做这一步', () => {
    const text = bar([row('1', '2026-09-25T10:04:58Z', { tool: 'Bash', arg: 'make test' })], true, RUNNING)
    expect(text).toContain('正在执行命令')
  })

  it('那一步已经挂了、之后再没动静：回到思考中，并说多久没动静了', () => {
    const text = bar(
      [row('1', '2026-09-25T10:04:00Z', { tool: 'Bash', arg: 'make test', failed: true, error: 'x' })],
      true,
      RUNNING
    )
    expect(text).toContain('思考中')
    expect(text).toContain('最近活动 1 分 00 秒前')
  })

  it('平台说它在重试：重试中，带着第几次', () => {
    const text = bar(
      [
        row('1', '2026-09-25T10:03:10Z', { tool: 'Bash', arg: 'ls' }),
        row('2', '2026-09-25T10:04:00Z', { event_type: 'api_retry', attempt: 3, at: '2026-09-25T10:04:50Z' }),
      ],
      true,
      RUNNING
    )
    expect(text).toContain('重试中（第 3 次）')
    // 重试那一行是原地改的，最近的动静按它最后一次改的时间算。
    expect(text).toContain('最近活动 10 秒前')
  })

  it('平台说它在等机器：等待机器；机器回来了就不再这么说', () => {
    const waiting = row('1', '2026-09-25T10:04:00Z', { event_type: 'device_waiting', state: 'waiting' })
    expect(bar([waiting], true, RUNNING)).toContain('等待机器')
    const back = row('1', '2026-09-25T10:04:00Z', { event_type: 'device_waiting', state: 'over' })
    expect(bar([back], true, RUNNING)).toContain('思考中')
  })

  it('运行环境还在起来、这一轮还没开始：等待机器', () => {
    const startup = row('1', '2026-09-25T10:04:30Z', { event_type: 'cloud_startup' }, null)
    expect(bar([startup], true)).toContain('等待机器')
  })

  it('上一轮的一步不算这一轮在做的事', () => {
    const old = row('1', '2026-09-25T10:00:00Z', { tool: 'Bash', arg: 'make test' }, 'turn-old')
    expect(bar([old], true, RUNNING)).toContain('思考中')
  })

  it('没有活在跑：空闲；上一轮以失败收场：已停止', () => {
    const done = row('1', '2026-09-25T10:00:00Z', { tool: 'Bash', arg: 'ls' })
    expect(bar([done], false)).toContain('空闲')
    const failed = row('2', '2026-09-25T10:00:05Z', { event_type: 'turn_failed', severity: 'error' })
    expect(bar([done, failed], false)).toContain('已停止')
  })

  it('一轮都没跑过、也没有任何记录：什么都不说', () => {
    expect(bar([], false)).toBe('')
  })
})
