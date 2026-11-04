/**
 * 「正在加载」这一行说到第几档：冷启动（第一次连上、机器还在起）能等一两分钟，热启动
 * 几秒就完。原来两处共用一句「正在加载新页面…」，读者等四十秒之后分不出「快好了」和
 * 「卡住了」，也看不到已经等了多久。
 */
import { expect, it } from 'vitest'

import { elapsedLabel, loadingTone, SLOW_AFTER_S, STUCK_AFTER_S } from './previewLoading'

it('几秒内照常说在做什么，十来秒说慢在哪，过了半分钟给下一步', () => {
  expect(loadingTone(0)).toBe('normal')
  expect(loadingTone(SLOW_AFTER_S - 1)).toBe('normal')
  expect(loadingTone(SLOW_AFTER_S)).toBe('slow')
  expect(loadingTone(STUCK_AFTER_S - 1)).toBe('slow')
  expect(loadingTone(STUCK_AFTER_S)).toBe('stuck')
  expect(loadingTone(10_000)).toBe('stuck')
})

it('几十秒按秒说，一分多钟之后按分说', () => {
  expect(elapsedLabel(0)).toEqual({ n: 0, unit: 'second' })
  expect(elapsedLabel(59)).toEqual({ n: 59, unit: 'second' })
  expect(elapsedLabel(60)).toEqual({ n: 1, unit: 'minute' })
  expect(elapsedLabel(135)).toEqual({ n: 2, unit: 'minute' })
  // 负数（时钟回拨、测试里没给起点）当作 0，不说「已等 -3 秒」。
  expect(elapsedLabel(-3)).toEqual({ n: 0, unit: 'second' })
})
