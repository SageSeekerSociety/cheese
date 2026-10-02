import { describe, expect, it } from 'vitest'

import { partialStringField } from './partialJson'

const content = (raw: string) => partialStringField(raw, 'content')

/** Every prefix of `json`, as a model streaming it would show them. */
function prefixes(json: string): string[] {
  return Array.from({ length: json.length + 1 }, (_, n) => json.slice(0, n))
}

describe('partialStringField', () => {
  it('reads the value as far as it has been written', () => {
    expect(content('{"content":"Hel')).toEqual({ text: 'Hel', closed: false })
    expect(content('{"content": "Hello"')).toEqual({ text: 'Hello', closed: true })
    expect(content('{"content":"Hello"}')).toEqual({ text: 'Hello', closed: true })
  })

  it('says nothing before the value has started', () => {
    for (const raw of ['', '{', '{"con', '{"content"', '{"content":', '{"content": ']) {
      expect(content(raw)).toBeNull()
    }
  })

  it('finds the field after others, whatever they hold', () => {
    const raw = '{"reply_to":"a\\"b","meta":{"content":"no","list":[1,"]"]},"n":12,"ok":true,"content":"yes'
    expect(content(raw)).toEqual({ text: 'yes', closed: false })
  })

  it('never takes a field of the same name from inside another value', () => {
    expect(content('{"meta":{"content":"nested"')).toBeNull()
    expect(content('{"reply_to":"\\"content\\":\\"x')).toBeNull()
  })

  it('decodes escapes, and never shows half of one', () => {
    const json = JSON.stringify({ content: 'line one\nsaid "hi" \\ tab\there' })
    const final = 'line one\nsaid "hi" \\ tab\there'
    for (const raw of prefixes(json)) {
      const got = content(raw)
      if (got === null) continue
      // Every intermediate reading is a prefix of the final text: no stray
      // backslash, no `n` where a newline belongs.
      expect(final.startsWith(got.text)).toBe(true)
    }
    expect(content(json)).toEqual({ text: final, closed: true })
  })

  it('decodes unicode escapes, including a pair split across reads', () => {
    const final = '芝士 says 😀 ok'
    // Escaped the way an ASCII-only encoder would write it.
    const escaped = final.replace(/[^\x20-\x7e]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
    const json = `{"content":"${escaped}"}`
    for (const raw of prefixes(json)) {
      const got = content(raw)
      if (got === null) continue
      expect(final.startsWith(got.text)).toBe(true)
      // No lone surrogate ever reaches the screen.
      expect(got.text).not.toMatch(/[\ud800-\udbff](?![\udc00-\udfff])/)
    }
    expect(content(json)).toEqual({ text: final, closed: true })
  })

  it('passes characters written raw straight through', () => {
    expect(content('{"content":"你好 😀')).toEqual({ text: '你好 😀', closed: false })
  })

  it('answers null for a value that is not a string, or input that is not an object', () => {
    expect(content('{"content":42}')).toBeNull()
    expect(content('["content","x"]')).toBeNull()
    expect(content('not json')).toBeNull()
  })
})
