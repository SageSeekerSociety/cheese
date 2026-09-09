import { afterEach, describe, expect, it, vi } from 'vitest'

import { resolveInitialLocale, setLocale } from './index'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.removeItem('cheese:locale')
})

describe('initial website language', () => {
  it('uses the browser language until the visitor chooses a language', () => {
    localStorage.removeItem('cheese:locale')
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('zh-TW')
    expect(resolveInitialLocale()).toBe('zh-CN')
    setLocale('en')
    expect(resolveInitialLocale()).toBe('en')
  })

  it('uses English for other browser languages and tolerates unavailable storage', () => {
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('fr-FR')
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    expect(resolveInitialLocale()).toBe('en')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    expect(() => setLocale('zh-CN')).not.toThrow()
  })
})
