import type { MemberActivityLine } from '@/lib/memberActivity'

import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentNowStrip from './AgentNowStrip.vue'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('en')
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-17T10:00:00Z'))
})
afterEach(() => vi.useRealTimers())

const NOW_S = Date.parse('2026-11-17T10:00:00Z') / 1000
const line = (over: Partial<MemberActivityLine>): MemberActivityLine => ({
  handle: 'cedar',
  name: 'Cedar',
  kind: 'working',
  since: NOW_S - 120,
  ...over,
})

describe('AI now: the strip at the top of a channel', () => {
  it('draws nothing when no teammate is working (typing does not count)', () => {
    const { container } = render(AgentNowStrip, {
      props: { lines: [line({ kind: 'typing', handle: 'alice', name: 'Alice' })] },
    })
    expect(container.querySelector('[data-testid="agent-now"]')).toBeNull()
  })

  it('one row per working teammate: the mark, the name and the step it is on', () => {
    const { container } = render(AgentNowStrip, {
      props: {
        lines: [
          line({ step: 'running pnpm test', lastFrameAt: NOW_S * 1000 - 2000 }),
          line({ handle: 'fir', name: 'Fir', detail: 'thinking' }),
        ],
      },
    })
    const rows = Array.from(container.querySelectorAll('.agent-now__row'))
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('.agent-now__name')?.textContent).toBe('Cedar')
    expect(rows[0].querySelector('.agent-now__text')?.textContent).toBe('running pnpm test')
    expect(rows[1].querySelector('.agent-now__name')?.textContent).toBe('Fir')
    expect(rows[1].querySelector('.agent-now__text')?.textContent).toBe('thinking')
    expect(container.querySelector('.mdi-timelapse')).not.toBeNull()
    expect(container.querySelector('.agent-now__dot')).toBeNull()
  })

  it('with no step it still says the teammate is working', () => {
    const { container } = render(AgentNowStrip, { props: { lines: [line({})] } })
    expect(container.textContent).toContain('Working')
  })

  it('30 minutes without a new frame: the hollow ring with the amber dot, and the hover says so', () => {
    const { container } = render(AgentNowStrip, {
      props: { lines: [line({ since: NOW_S - 3600, lastFrameAt: (NOW_S - 2400) * 1000, step: 'running e2e' })] },
    })
    expect(container.querySelector('.mdi-circle-outline')).not.toBeNull()
    expect(container.querySelector('.agent-now__dot')).not.toBeNull()
    expect(container.querySelector('.agent-now__row')?.getAttribute('title')).toContain("hasn't changed")
  })

  it('a teammate waiting on an automatic retry shows the warning mark', () => {
    const { container } = render(AgentNowStrip, { props: { lines: [line({ kind: 'retrying' })] } })
    expect(container.querySelector('.mdi-alert-circle')).not.toBeNull()
    expect(container.textContent).toContain('retrying automatically')
  })

  it('a teammate stopped on an expired login: a red row with the reason and a sign-in button', async () => {
    const failure = {
      handle: 'fir',
      name: 'Fir',
      code: 'login_expired',
      since: new Date(NOW_S * 1000 - 40 * 60_000).toISOString(),
    }
    const { container, emitted } = render(AgentNowStrip, { props: { lines: [], failures: [failure] } })
    const row = container.querySelector('.agent-now__row--failed') as HTMLElement
    expect(row.getAttribute('data-code')).toBe('login_expired')
    expect(row.textContent).toContain('Fir')
    expect(row.textContent).toContain('Login expired')
    expect(row.getAttribute('title')).toContain('expired login')
    const button = container.querySelector('[data-testid="agent-now-relogin"]') as HTMLButtonElement
    button.click()
    expect(emitted().relogin).toEqual([['fir']])
  })

  it('a model error is red too, but offers no sign-in button', () => {
    const failure = {
      handle: 'fir',
      name: 'Fir',
      code: 'model_error',
      since: new Date(NOW_S * 1000 - 600_000).toISOString(),
    }
    const { container } = render(AgentNowStrip, { props: { lines: [], failures: [failure] } })
    expect(container.querySelector('.agent-now__row--failed')).not.toBeNull()
    expect(container.querySelector('.mdi-alert-circle')).not.toBeNull()
    expect(container.querySelector('[data-testid="agent-now-relogin"]')).toBeNull()
  })
})
