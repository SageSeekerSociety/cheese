// 那份「本次会话取不到的头像」记忆（lib/identityFace 的正文小脸靠背景图画图，坏图不触发
// 任何事件，得靠这里的探针把失败补记回来）。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { isAvatarKnownFailed, probeAvatarFailure, rememberAvatarFailure } from './avatarFailures'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('probeAvatarFailure', () => {
  it('records the URL as failed when its picture errors', () => {
    const url = 'https://x/avatars/probe-1'
    const spy = vi.spyOn(document, 'createElement')
    probeAvatarFailure(url)
    // 探针是一个从不插入文档的 <img>：只有这样才拿得到背景图拿不到的那次 error。
    const probe = spy.mock.results.map((r) => r.value as HTMLImageElement).find((el) => el?.tagName === 'IMG')!
    expect(probe.getAttribute('src')).toBe(url)
    expect(isAvatarKnownFailed(url)).toBe(false)
    probe.dispatchEvent(new Event('error'))
    expect(isAvatarKnownFailed(url)).toBe(true)
  })

  it('probes each URL once and skips one already known to fail', () => {
    const url = 'https://x/avatars/probe-2'
    rememberAvatarFailure(url)
    const spy = vi.spyOn(document, 'createElement')
    probeAvatarFailure(url)
    expect(spy.mock.results.some((r) => (r.value as HTMLElement)?.tagName === 'IMG')).toBe(false)
  })

  it('does nothing for an empty URL', () => {
    const spy = vi.spyOn(document, 'createElement')
    probeAvatarFailure('')
    expect(spy.mock.results.some((r) => (r.value as HTMLElement)?.tagName === 'IMG')).toBe(false)
  })
})
