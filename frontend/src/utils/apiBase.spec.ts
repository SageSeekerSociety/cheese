import { describe, expect, it } from 'vitest'

import { apiBaseAllowed } from './apiBase'

describe('apiBaseAllowed', () => {
  it('accepts https, an empty value and the metering proxy ChatGPT entry', () => {
    for (const value of [
      '',
      'https://open.bigmodel.cn/api/anthropic',
      'http://metering-proxy:8445/chatgpt/work',
      'http://metering-proxy:8445/chatgpt/spare-2.a_b/',
      ' http://metering-proxy:8445/chatgpt/work ',
    ]) {
      expect(apiBaseAllowed(value), value).toBe(true)
    }
  })

  it('refuses every other plain http address', () => {
    for (const value of [
      'http://metering-proxy.evil:8445/chatgpt/work',
      'http://metering-proxy:8446/chatgpt/work',
      'http://metering-proxy/chatgpt/work',
      'http://evil@metering-proxy:8445/chatgpt/work',
      'http://metering-proxy:8445@evil/chatgpt/work',
      'http://metering-proxy:8445/chatgpt/',
      'http://metering-proxy:8445/chatgpt/work/responses',
      'http://metering-proxy:8445/chatgpt/work?x=1',
      'http://172.17.0.1:8445/chatgpt/work',
      'http://litellm:4000',
    ]) {
      expect(apiBaseAllowed(value), value).toBe(false)
    }
  })
})
