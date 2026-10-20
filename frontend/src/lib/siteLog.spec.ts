import { describe, expect, it, vi } from 'vitest'

import { shouldKeepPinning, SITE_TAIL_PIN_FRAMES } from './siteLog'
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

describe('打开现场要真的落到底，不是「试过一次」', () => {
  // #327 已经写对了「要滚到底」这件事，线上还是停在最顶上。原因不在逻辑，
  // 在时机：面板先渲染 spinner，那一刻容器只有一屏高、根本没有可滚的量，
  // `scrollTop = scrollHeight` 被夹回 0；等真正的时间线铺开，已经没有人再滚
  // 一次了。部署后实测：+40ms 时 scrollHeight=500，+120ms 变成 2066，
  // scrollTop 全程 0。所以下面测的是「高度还在长就再钉一帧」这条规则。

  it('高度还在长的时候继续钉', () => {
    expect(shouldKeepPinning(2066, 500, 1)).toBe(true)
  })

  it('高度稳住的那一帧就停', () => {
    // 不停的话，它会一直和读者自己的滚动打架。
    expect(shouldKeepPinning(2066, 2066, 1)).toBe(false)
  })

  it('第一帧也算「在长」——lastHeight 的初值不能被当成已稳定', () => {
    // 初值必须是一个真实高度取不到的数（-1），否则「一开始就等于 0」的空面板
    // 会被判成稳定，一帧都不钉，等于没修。
    expect(shouldKeepPinning(500, -1, 0)).toBe(true)
  })

  it('帧数封顶，永远长不完的面板也会松手', () => {
    // 一轮正在跑的时候现场会持续追加，没有这个上限它会钉到页面关掉为止。
    expect(shouldKeepPinning(3000, 2900, SITE_TAIL_PIN_FRAMES)).toBe(false)
    expect(shouldKeepPinning(3000, 2900, SITE_TAIL_PIN_FRAMES - 1)).toBe(true)
  })
})
