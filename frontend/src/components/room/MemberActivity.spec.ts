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

  it('nobody busy: nothing is said, unless the line keeps its place', () => {
    const empty = render(MemberActivity, { props: { lines: [] } })
    expect(empty.container.querySelector('[data-testid="member-activity"]')).toBeNull()
    const kept = render(MemberActivity, { props: { lines: [], reserve: true } })
    const line = kept.container.querySelector('[data-testid="member-activity"]')
    expect(line).not.toBeNull()
    expect(line?.textContent?.trim()).toBe('')
  })

  it('says it in the reader’s language', () => {
    setLocale('zh-CN')
    const { container } = render(MemberActivity, { props: { lines: [typing('Alice'), typing('Bob')] } })
    expect(lines(container)).toEqual(['Alice和Bob正在输入…'])
  })
})
