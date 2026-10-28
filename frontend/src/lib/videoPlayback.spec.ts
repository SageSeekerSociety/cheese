import { describe, expect, it } from 'vitest'

import { VIDEO_RATES, connectionInfo, formatVideoTime, nextVideoRate, prefersLightVideo } from './videoPlayback'

describe('formatVideoTime', () => {
  it('pads the seconds and only grows an hours field when there is one', () => {
    expect(formatVideoTime(0)).toBe('0:00')
    expect(formatVideoTime(7)).toBe('0:07')
    expect(formatVideoTime(62)).toBe('1:02')
    expect(formatVideoTime(3723)).toBe('1:02:03')
  })

  it('reads an unknown position as 0:00, never as NaN', () => {
    // 元数据还没回来时控件上不该空着或出现 NaN:NaN —— 那是「坏了」的样子。
    expect(formatVideoTime(Number.NaN)).toBe('0:00')
    expect(formatVideoTime(null)).toBe('0:00')
    expect(formatVideoTime(undefined)).toBe('0:00')
    expect(formatVideoTime(-1)).toBe('0:00')
  })
})

describe('nextVideoRate', () => {
  it('walks the list and wraps', () => {
    expect(VIDEO_RATES[0]).toBe(0.5)
    expect(nextVideoRate(1)).toBe(1.25)
    expect(nextVideoRate(2)).toBe(0.5)
  })

  it('falls back to the first entry for a rate the list does not have', () => {
    expect(nextVideoRate(3.5)).toBe(VIDEO_RATES[0])
  })
})

describe('prefersLightVideo', () => {
  it('is true on save-data and on the slowest networks', () => {
    expect(prefersLightVideo({ saveData: true })).toBe(true)
    expect(prefersLightVideo({ effectiveType: '2g' })).toBe(true)
    expect(prefersLightVideo({ effectiveType: 'slow-2g' })).toBe(true)
  })

  it('is false when the browser does not say, instead of holding everyone back', () => {
    // Firefox 与 Safari 没有 navigator.connection。读不到就把所有人的视频都扣着，
    // 比多取一帧坏得多。
    expect(prefersLightVideo(null)).toBe(false)
    expect(prefersLightVideo(undefined)).toBe(false)
    expect(prefersLightVideo({})).toBe(false)
    expect(prefersLightVideo({ effectiveType: '4g' })).toBe(false)
  })
})

describe('connectionInfo', () => {
  it('hands back the browser connection object, or null when there is none', () => {
    const original = navigator.connection
    Object.defineProperty(navigator, 'connection', { value: { effectiveType: '4g' }, configurable: true })
    try {
      expect(connectionInfo()).toEqual({ effectiveType: '4g' })
    } finally {
      Object.defineProperty(navigator, 'connection', { value: original, configurable: true })
    }
  })
})
