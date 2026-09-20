// 安装引导的判据：什么时候有得装、什么时候算是装了、iOS 怎么认。
//
// 这一层全是**浏览器给的事实**，没有我们自己的状态机，所以用例钉的是三条容易写错
// 的地方：事件必须 preventDefault（否则 Chromium 自己那条提示会和我们打架）、
// 一次机会只能用一次、以及 iOS 的 display-mode 永远不匹配（只能看它自己的
// navigator.standalone）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetInstallPromptForTests,
  canPromptInstall,
  detectIos,
  detectStandalone,
  isInstalled,
  promptInstall,
  watchInstallPrompt,
} from './pwaInstall'

let stopWatching: (() => void) | null = null

/** 造一个 Chromium 会发的那种事件：prompt() + userChoice。 */
function installEvent(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: () => Promise<void>
    userChoice: Promise<{ outcome: string; platform: string }>
  }
  event.prompt = vi.fn(async () => {})
  event.userChoice = Promise.resolve({ outcome, platform: 'web' })
  return event
}

function fakeMatchMedia(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: matches && query.includes('display-mode: standalone'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

function fakeNavigator(overrides: Record<string, unknown>) {
  vi.stubGlobal('navigator', {
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    platform: 'Linux x86_64',
    maxTouchPoints: 0,
    ...overrides,
  })
}

beforeEach(() => {
  __resetInstallPromptForTests()
  fakeMatchMedia(false)
  fakeNavigator({})
})

afterEach(() => {
  stopWatching?.()
  stopWatching = null
  vi.unstubAllGlobals()
  __resetInstallPromptForTests()
})

describe('安装机会', () => {
  it('接住 beforeinstallprompt，并且挡住浏览器自己那条提示', () => {
    stopWatching = watchInstallPrompt()
    const event = installEvent()
    window.dispatchEvent(event)

    expect(canPromptInstall.value).toBe(true)
    // 不 preventDefault 的话 Chromium 会自己弹一次，两边抢同一个时机。
    expect(event.defaultPrevented).toBe(true)
  })

  it('调用 prompt() 并把用户的选择带回来；这次机会用掉就没了', async () => {
    stopWatching = watchInstallPrompt()
    const event = installEvent('accepted')
    window.dispatchEvent(event)

    await expect(promptInstall()).resolves.toBe('accepted')
    expect(event.prompt).toHaveBeenCalledTimes(1)
    // 同一个事件再 prompt() 一次是无效的，所以用完就扔。
    expect(canPromptInstall.value).toBe(false)
    await expect(promptInstall()).resolves.toBe('unavailable')
  })

  it('用户在原生弹窗里取消，返回 dismissed', async () => {
    stopWatching = watchInstallPrompt()
    window.dispatchEvent(installEvent('dismissed'))
    await expect(promptInstall()).resolves.toBe('dismissed')
  })

  it('没有机会的时候 promptInstall 什么都不做，也不会抛', async () => {
    stopWatching = watchInstallPrompt()
    await expect(promptInstall()).resolves.toBe('unavailable')
  })

  it('appinstalled 之后算装好了，并且不再提供安装入口', () => {
    stopWatching = watchInstallPrompt()
    window.dispatchEvent(installEvent())
    window.dispatchEvent(new Event('appinstalled'))

    expect(isInstalled.value).toBe(true)
    expect(canPromptInstall.value).toBe(false)
  })
})

describe('算不算已经装好了', () => {
  it('display-mode: standalone 就是装了', () => {
    fakeMatchMedia(true)
    expect(detectStandalone()).toBe(true)
  })

  it('iOS 不看 display-mode，看它自己的 navigator.standalone', () => {
    fakeNavigator({ standalone: true })
    expect(detectStandalone()).toBe(true)
    fakeNavigator({ standalone: false })
    expect(detectStandalone()).toBe(false)
  })

  it('普通浏览器里打开的页面不算装了', () => {
    expect(detectStandalone()).toBe(false)
  })
})

describe('是不是 iOS', () => {
  it('iPhone / iPad 的 UA', () => {
    fakeNavigator({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)' })
    expect(detectIos()).toBe(true)
    fakeNavigator({ userAgent: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)' })
    expect(detectIos()).toBe(true)
  })

  it('iPadOS 13 起自称 Mac，靠触摸点数认出来', () => {
    fakeNavigator({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)',
      platform: 'MacIntel',
      maxTouchPoints: 5,
    })
    expect(detectIos()).toBe(true)
  })

  it('真的 Mac 不认（没有触摸屏）', () => {
    fakeNavigator({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)',
      platform: 'MacIntel',
      maxTouchPoints: 0,
    })
    expect(detectIos()).toBe(false)
  })
})

describe('重复挂监听', () => {
  it('挂第二次不会挂出第二份监听', () => {
    stopWatching = watchInstallPrompt()
    watchInstallPrompt()
    // 第一次那个卸载之后，不该还有谁接得住事件。
    stopWatching()
    stopWatching = null

    const after = installEvent()
    window.dispatchEvent(after)
    expect(after.defaultPrevented).toBe(false)
    expect(canPromptInstall.value).toBe(false)
  })
})
