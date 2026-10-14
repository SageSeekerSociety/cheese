import type { MemberActivityLine } from '@/lib/memberActivity'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MemberActivity from './MemberActivity.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(() => vi.useRealTimers())

const plugins = [createVuetify({ components, directives }), i18n]

const NO_FACE = { avatar: null, agent: false }

function typing(name: string): MemberActivityLine {
  return { handle: name.toLowerCase(), name, face: NO_FACE, kind: 'typing', since: 0 }
}

/** 一位在干活的成员；没说的部分照默认填。 */
function working(over: Partial<MemberActivityLine> = {}): MemberActivityLine {
  return { handle: 'cheese-a1', name: 'Cedar', face: NO_FACE, kind: 'working', since: 0, ...over }
}

/**
 * 每一行读出来的字：把头像那块抠掉再读 —— 没有真图时头像会画一个彩色首字母，那是装饰
 * （`data-user-content`），不是这一行要说的话。
 */
function lines(container: Element): string[] {
  return Array.from(container.querySelectorAll('.member-activity__line')).map((el) => {
    const clone = el.cloneNode(true) as Element
    clone.querySelectorAll('[data-user-content]').forEach((node) => node.remove())
    return clone.textContent?.trim() ?? ''
  })
}

describe('who is busy in the room, under the composer', () => {
  it('one person typing is named', () => {
    const { container } = render(MemberActivity, { props: { lines: [typing('Alice')] }, global: { plugins } })
    expect(lines(container)).toEqual(['Alice is typing…'])
  })

  it('two people typing are both named in one line', () => {
    const { container } = render(MemberActivity, {
      props: { lines: [typing('Alice'), typing('Bob')] },
      global: { plugins },
    })
    expect(lines(container)).toEqual(['Alice and Bob are typing…'])
  })

  it('three or more typing become "several people"', () => {
    const { container } = render(MemberActivity, {
      props: { lines: [typing('Alice'), typing('Bob'), typing('Chen')] },
      global: { plugins },
    })
    expect(lines(container)).toEqual(['Several people are typing…'])
  })

  it('an agent with a turn running is working, with what it is doing and for how long', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [working({ since, detail: 'Thinking' }), typing('Alice')],
      },
      global: { plugins },
    })
    const [atWork, people] = lines(container)
    expect(atWork).toMatch(/^Cedar is working… · Thinking · 2.*05/)
    expect(people).toBe('Alice is typing…')
  })

  it('shows the step the agent is on, in place of the phase label', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [working({ since, detail: 'Thinking', step: 'Running a command pnpm test' })],
      },
      global: { plugins },
    })
    expect(lines(container)[0]).toMatch(/^Cedar is working… · Running a command pnpm test · 2.*05/)
  })

  it('says how long there has been no new output, once it passes a minute', () => {
    vi.useFakeTimers()
    const now = Date.parse('2026-10-01T10:02:00Z')
    vi.setSystemTime(now)
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: { lines: [working({ since, detail: 'Thinking', lastFrameAt: now - 80_000 })] },
      global: { plugins },
    })
    const [line] = lines(container)
    expect(line).toContain('No new output for 1m 20s')
    expect(line).toContain('2m') // alongside the total time, not instead of it
  })

  it('says nothing about a stall left over from an earlier turn', () => {
    vi.useFakeTimers()
    const now = Date.parse('2026-10-01T10:02:00Z')
    vi.setSystemTime(now)
    // The stretch began 4s ago; the frame we hold is the one the previous turn left behind.
    const since = (now - 4_000) / 1000
    const { container } = render(MemberActivity, {
      props: { lines: [working({ since, detail: 'Thinking', lastFrameAt: now - 127_000 })] },
      global: { plugins },
    })
    expect(lines(container)[0]).not.toContain('No new output')
  })

  it('does not carry the previous turn’s step into a stretch that just began', () => {
    vi.useFakeTimers()
    const now = Date.parse('2026-10-01T10:02:00Z')
    vi.setSystemTime(now)
    const since = (now - 4_000) / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [
          working({ since, detail: 'Thinking', step: 'Running a command pnpm test', lastFrameAt: now - 127_000 }),
        ],
      },
      global: { plugins },
    })
    expect(lines(container)[0]).not.toContain('Running a command pnpm test')
  })

  it('says nothing about stalls while output is still arriving', () => {
    vi.useFakeTimers()
    const now = Date.parse('2026-10-01T10:02:00Z')
    vi.setSystemTime(now)
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: { lines: [working({ since, detail: 'Thinking', lastFrameAt: now - 5_000 })] },
      global: { plugins },
    })
    expect(lines(container)[0]).not.toContain('No new output')
  })

  it('tells a screen reader who is working and which phase, without the seconds churning', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.parse('2026-10-01T10:02:05Z'))
    const since = Date.parse('2026-10-01T10:00:00Z') / 1000
    const { container } = render(MemberActivity, {
      props: {
        lines: [working({ since, detail: 'Thinking', step: 'Running a command pnpm test' })],
      },
      global: { plugins },
    })
    const announced = container.querySelector('.visually-hidden')?.textContent?.trim()
    expect(announced).toBe('Cedar is working… · Thinking')
  })

  it('nobody busy: nothing is said and no room is kept for it', () => {
    const empty = render(MemberActivity, { props: { lines: [] }, global: { plugins } })
    expect(empty.container.querySelector('[data-testid="member-activity"]')).toBeNull()
  })

  it('says it in the reader’s language', () => {
    setLocale('zh-CN')
    const { container } = render(MemberActivity, {
      props: { lines: [typing('Alice'), typing('Bob')] },
      global: { plugins },
    })
    expect(lines(container)).toEqual(['Alice和Bob正在输入…'])
  })

  it('draws the working teammate as a face beside its name, not a bare name', () => {
    const { container } = render(MemberActivity, {
      props: { lines: [working({ handle: 'cheese-a1', name: 'Cedar', face: { avatar: null, agent: true } })] },
      global: { plugins },
    })

    const line = container.querySelector('[data-kind="working"]') as HTMLElement
    expect(line.querySelector('.identity-view'), '这一行没画身份').not.toBeNull()
    // 队友画超椭圆，不画真人的圆头像。
    expect(line.querySelector('.cheese-avatar')).not.toBeNull()
    // 名字只写一遍：句子里的名字就是那张脸上的名字配文。
    expect(line.querySelectorAll('.identity-view__name').length, '名字被写了两遍').toBe(1)
    expect(line.querySelector('.identity-view__name')?.textContent).toBe('Cedar')
  })

  it('draws a person’s own avatar when the source knows it', () => {
    const { container } = render(MemberActivity, {
      props: {
        lines: [working({ handle: 'alice', name: 'Alice', face: { avatar: '/avatars/7', agent: false } })],
      },
      global: { plugins },
    })

    const line = container.querySelector('[data-kind="working"]') as HTMLElement
    expect(line.querySelector('img')?.getAttribute('src')).toContain('/avatars/7')
  })

  it('draws each typist as a face too', () => {
    const { container } = render(MemberActivity, {
      props: {
        lines: [typing('Alice'), { ...typing('Bob'), face: { avatar: null, agent: true }, handle: 'cheese-b1' }],
      },
      global: { plugins },
    })

    const line = container.querySelector('[data-kind="typing"]') as HTMLElement
    expect(line.querySelectorAll('.identity-view').length).toBe(2)
    expect(line.querySelector('.cheese-avatar'), '队友没画超椭圆').not.toBeNull()
  })
})
