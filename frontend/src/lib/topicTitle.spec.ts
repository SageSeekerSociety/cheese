import { describe, expect, it } from 'vitest'

import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from './topicTitle'

describe('normalizeTopicTitle', () => {
  it('trims surrounding whitespace', () => {
    expect(normalizeTopicTitle('  新标题  ', 'old')).toBe('新标题')
  })

  it('rejects a blank or whitespace-only draft', () => {
    expect(normalizeTopicTitle('', 'old')).toBeNull()
    expect(normalizeTopicTitle('   ', 'old')).toBeNull()
  })

  it('rejects a draft that is unchanged after trimming', () => {
    expect(normalizeTopicTitle('old', 'old')).toBeNull()
    expect(normalizeTopicTitle('  old  ', 'old')).toBeNull()
  })

  it('caps length to mirror the backend silent truncation at 80 chars', () => {
    const long = 'a'.repeat(100)
    const result = normalizeTopicTitle(long, 'old')
    expect(result).toHaveLength(TOPIC_TITLE_MAX_LENGTH)
    expect(result).toBe('a'.repeat(TOPIC_TITLE_MAX_LENGTH))
  })

  it('accepts a custom max length', () => {
    expect(normalizeTopicTitle('abcdef', 'old', 3)).toBe('abc')
  })
})
