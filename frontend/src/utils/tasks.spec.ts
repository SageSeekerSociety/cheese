import type { Task } from '@/types'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { getTaskStatusText, getTaskStatusType } from './tasks'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

afterEach(() => vi.useRealTimers())

describe('task registration status', () => {
  it.each([
    { deadline: null, registrationStartAt: undefined, status: 'ongoing', color: 'primary' },
    { deadline: null, registrationStartAt: 2000, status: 'notStarted', color: 'info' },
    { deadline: 500, registrationStartAt: undefined, status: 'ended', color: 'error' },
    { deadline: 2000, registrationStartAt: undefined, status: 'ongoing', color: 'primary' },
  ])('shows $status for deadline=$deadline and start=$registrationStartAt', ({ status, color, ...dates }) => {
    vi.useFakeTimers()
    vi.setSystemTime(1000)
    const task = { approved: 'APPROVED', ...dates } as Task
    expect(getTaskStatusText(task)).toBe(`tasks.status.${status}`)
    expect(getTaskStatusType(task)).toBe(color)
  })
})
