import type { MemberActivityLine } from '@/lib/memberActivity'

import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MemberActivity from './MemberActivity.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(() => vi.useRealTimers())

function typing(name: string): MemberActivityLine {
  return { handle: name.toLowerCase(), name, kind: 'typing', since: 0 }
}

function lines(container: Element): string[] {
  return Array.from(container.querySelectorAll('.member-activity__line')).map((el) => el.textContent?.trim() ?? '')
}

describe('who is busy in the room, under the composer', () => {
  it('one person typing is named', () => {
    const { container } = render(MemberActivity, { props: { lines: [typing('Alice')] } })
    expect(lines(container)).toEqual(['Alice is typing…'])
  })

  it('two people typing are both named in one line', () => {
    const { container } = render(MemberActivity, { props: { lines: [typing('Alice'), typing('Bob')] } })
    expect(lines(container)).toEqual(['Alice and Bob are typing…'])
  })

  it('three or more typing become "several people"', () => {
    const { container } = render(MemberActivity, {
      props: { lines: [typing('Alice'), typing('Bob'), typing('Chen')] },
    })
    expect(lines(container)).toEqual(['Several people are typing…'])
  })

  it('an agent with a turn running is working, with what it is doing and for how long', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [{ handle: 'cheese-a1', name: 'Cedar', kind: 'working', since, detail: 'Thinking' }, typing('Alice')],
      },
    })
    const [working, people] = lines(container)
    expect(working).toMatch(/^Cedar is working… · Thinking · 2.*05/)
    expect(people).toBe('Alice is typing…')
  })

  it('shows the step the agent is on, in place of the phase label', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [
          {
            handle: 'cheese-a1',
            name: 'Cedar',
            kind: 'working',
            since,
            detail: 'Thinking',
            step: 'Running a command pnpm test',
          },
        ],
      },
    })
    expect(lines(container)[0]).toMatch(/^Cedar is working… · Running a command pnpm test · 2.*05/)
  })

  it('does not carry the previous turn\u2019s step into a stretch that just began', () => {
    vi.useFakeTimers()
    const now = Date.parse('2026-10-01T10:02:00Z')
    vi.setSystemTime(now)
    const since = (now - 4_000) / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [
          {
            handle: 'cheese-a1',
            name: 'Cedar',
            kind: 'working',
            since,
            detail: 'Thinking',
            step: 'Running a command pnpm test',
            lastFrameAt: now - 127_000,
          },
        ],
      },
    })
    expect(lines(container)[0]).not.toContain('Running a command pnpm test')
  })

  it('tells a screen reader who is working and which phase, without the seconds churning', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [
          {
            handle: 'cheese-a1',
            name: 'Cedar',
            kind: 'working',
            since,
            detail: 'Thinking',
            step: 'Running a command pnpm test',
          },
        ],
      },
    })
    const announced = container.querySelector('.visually-hidden')?.textContent?.trim()
    expect(announced).toBe('Cedar is working… · Thinking')
  })

  it('nobody busy: nothing is said and no room is kept for it', () => {
    const empty = render(MemberActivity, { props: { lines: [] } })
    expect(empty.container.querySelector('[data-testid="member-activity"]')).toBeNull()
  })

  it('says it in the reader’s language', () => {
    setLocale('zh-CN')
    const { container } = render(MemberActivity, { props: { lines: [typing('Alice'), typing('Bob')] } })
    expect(lines(container)).toEqual(['Alice和Bob正在输入…'])
  })
})
