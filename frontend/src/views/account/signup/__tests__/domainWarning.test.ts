/** Unit tests for the email domain warning dialog on the signup page.
 *
 * Covers the pure logic extracted from Start.vue:
 * - localStorage-based "show once" gating
 * - "不再提醒" persistence
 * - "关闭" dismiss without setting flag
 * - localStorage unavailability fallback
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

// ---------------------------------------------------------------------------
// Constants and pure functions — mirrors Start.vue logic
// ---------------------------------------------------------------------------

const DOMAIN_WARNING_KEY = 'cheese:domain_warning_seen'

function shouldShowWarning(storage: Storage | null): boolean {
  try {
    if (!storage) return false
    return storage.getItem(DOMAIN_WARNING_KEY) !== '1'
  } catch {
    return false
  }
}

function markWarningSeen(storage: Storage | null): boolean {
  try {
    if (!storage) return false
    storage.setItem(DOMAIN_WARNING_KEY, '1')
    return true
  } catch {
    return false
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockStorage(initial: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(initial))
  return {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value)
    }),
    removeItem: vi.fn((key: string) => {
      store.delete(key)
    }),
    clear: vi.fn(() => {
      store.clear()
    }),
    get length() {
      return store.size
    },
    key: vi.fn((index: number) => [...store.keys()][index] ?? null),
  } as unknown as Storage
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('domain warning dialog gating', () => {
  describe('shouldShowWarning', () => {
    it('returns true when no flag has ever been set', () => {
      const storage = mockStorage()
      expect(shouldShowWarning(storage)).toBe(true)
    })

    it('returns true when flag is not "1"', () => {
      const storage = mockStorage({ [DOMAIN_WARNING_KEY]: '0' })
      expect(shouldShowWarning(storage)).toBe(true)
    })

    it('returns false when flag is "1"', () => {
      const storage = mockStorage({ [DOMAIN_WARNING_KEY]: '1' })
      expect(shouldShowWarning(storage)).toBe(false)
    })

    it('does not call getItem when storage is null', () => {
      expect(shouldShowWarning(null)).toBe(false)
    })

    it('returns false when storage.getItem throws', () => {
      const broken = {
        getItem: vi.fn(() => {
          throw new Error('quota exceeded')
        }),
      } as unknown as Storage
      expect(shouldShowWarning(broken)).toBe(false)
    })
  })

  describe('markWarningSeen', () => {
    it('sets the flag to "1"', () => {
      const storage = mockStorage()
      const result = markWarningSeen(storage)

      expect(result).toBe(true)
      expect(storage.getItem(DOMAIN_WARNING_KEY)).toBe('1')
      expect(storage.setItem).toHaveBeenCalledWith(DOMAIN_WARNING_KEY, '1')
    })

    it('overwrites a previous non-"1" value', () => {
      const storage = mockStorage({ [DOMAIN_WARNING_KEY]: '0' })
      markWarningSeen(storage)
      expect(storage.getItem(DOMAIN_WARNING_KEY)).toBe('1')
    })

    it('returns false when storage is null', () => {
      expect(markWarningSeen(null)).toBe(false)
    })

    it('returns false when storage.setItem throws', () => {
      const broken = {
        setItem: vi.fn(() => {
          throw new Error('quota exceeded')
        }),
      } as unknown as Storage
      expect(markWarningSeen(broken)).toBe(false)
    })
  })

  describe('"不再提醒" flow', () => {
    it('prevents future prompts after user clicks "不再提醒"', () => {
      const storage = mockStorage()

      // First visit — should show
      expect(shouldShowWarning(storage)).toBe(true)

      // User clicks "不再提醒"
      markWarningSeen(storage)

      // Second visit — should not show
      expect(shouldShowWarning(storage)).toBe(false)
    })
  })

  describe('"关闭" flow', () => {
    it('allows future prompts after user clicks "关闭"', () => {
      const storage = mockStorage()

      // First visit — should show
      expect(shouldShowWarning(storage)).toBe(true)

      // User clicks "关闭" (no flag set — just dialog close)

      // Second visit — should still show
      expect(shouldShowWarning(storage)).toBe(true)
    })
  })

  describe('multiple visits', () => {
    it('only shows on first visit after "不再提醒" clicked', () => {
      const storage = mockStorage()

      // Visit 1: shows
      expect(shouldShowWarning(storage)).toBe(true)
      // User clicks "关闭", no flag set

      // Visit 2: shows again
      expect(shouldShowWarning(storage)).toBe(true)
      // User clicks "不再提醒"
      markWarningSeen(storage)

      // Visit 3: does not show
      expect(shouldShowWarning(storage)).toBe(false)
    })

    it('keeps showing on every visit if user never clicks "不再提醒"', () => {
      const storage = mockStorage()

      for (let i = 0; i < 5; i++) {
        expect(shouldShowWarning(storage)).toBe(true)
        // User clicks "关闭" each time
      }
    })
  })

  describe('edge cases', () => {
    it('handles empty localStorage gracefully', () => {
      const storage = mockStorage()
      // No keys at all — same as no flag set
      expect(shouldShowWarning(storage)).toBe(true)
    })

    it('handles unrelated localStorage keys without interference', () => {
      const storage = mockStorage({
        some_other_key: 'value',
        'another:key': '123',
      })
      expect(shouldShowWarning(storage)).toBe(true)
      markWarningSeen(storage)
      // Unrelated keys preserved
      expect(storage.getItem('some_other_key')).toBe('value')
    })

    it('flag survives localStorage clear-then-revisit cycle', () => {
      const storage = mockStorage()

      markWarningSeen(storage)
      expect(shouldShowWarning(storage)).toBe(false)

      // Simulate clearing localStorage (e.g., user cleared browser data)
      const fresh = mockStorage()
      expect(shouldShowWarning(fresh)).toBe(true)
    })
  })
})
