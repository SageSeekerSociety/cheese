import { afterEach, describe, expect, it } from 'vitest'

import { setLocale } from '@/i18n'
import { formatRoutineTime } from '@/lib/routine'

const iso = '2026-10-07T09:00:00Z'

afterEach(() => setLocale('zh-CN'))

describe('routine times', () => {
  it('uses the interface language while keeping the rule timezone', () => {
    setLocale('en')
    expect(formatRoutineTime(iso, 'Asia/Shanghai')).toBe(
      new Date(iso).toLocaleString('en', { timeZone: 'Asia/Shanghai', hour12: false })
    )
    expect(formatRoutineTime(iso, 'Asia/Shanghai')).not.toBe(
      new Date(iso).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    )
    setLocale('zh-CN')
    expect(formatRoutineTime(iso, 'Asia/Shanghai')).toBe(
      new Date(iso).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    )
  })

  it('keeps the interface language if the stored timezone is invalid', () => {
    setLocale('en')
    expect(formatRoutineTime(iso, 'Invalid/Timezone')).toBe(new Date(iso).toLocaleString('en', { hour12: false }))
  })

  it('does not invent a time for an unscheduled rule', () => {
    expect(formatRoutineTime(null)).toBe('—')
  })
})
