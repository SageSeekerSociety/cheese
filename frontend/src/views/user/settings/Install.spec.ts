// 「安装到手机」这一页要说清三件不同的事，取决于你在哪个浏览器里：
//   * 能给安装按钮就给（Chromium 的 beforeinstallprompt）
//   * 给不了就写清「点哪几下」，iOS 和 Android/桌面的那几下不一样
//   * 已经装好了就别再劝人装
// 另外那句「请用 Chrome 装」必须一直在——真机上踩过的就是这一条：国产浏览器的
// 「添加到桌面」装出来的是壳，壳里选不了文件、也收不到推送。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import Install from './Install.vue'

import i18n, { setLocale } from '@/i18n'
import { __resetInstallPromptForTests, watchInstallPrompt } from '@/lib/pwaInstall'

const Page = Install as unknown as Parameters<typeof render>[0]

let vuetify: ReturnType<typeof createVuetify>
let stopWatching: (() => void) | null = null
const realMatchMedia = window.matchMedia?.bind(window)

/** 只让 `display-mode: standalone` 那条查询命中，其余照旧（Vuetify 自己也要用）。 */
function stubStandalone(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => {
    if (query.includes('display-mode: standalone')) {
      return { matches, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }
    }
    return realMatchMedia
      ? realMatchMedia(query)
      : { matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }
  })
}

function stubUserAgent(userAgent: string, extras: Record<string, unknown> = {}) {
  vi.stubGlobal('navigator', { userAgent, platform: 'Linux x86_64', maxTouchPoints: 0, ...extras })
}

function announceInstallOpportunity() {
  const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: () => Promise<void>
    userChoice: Promise<{ outcome: string; platform: string }>
  }
  event.prompt = vi.fn(async () => {})
  event.userChoice = Promise.resolve({ outcome: 'accepted', platform: 'web' })
  window.dispatchEvent(event)
  return event
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

/**
 * 重新挂一次监听。display-mode 是在挂监听那一刻读进来的（应用里就是启动那一刻），
 * 所以用例要换 UA / 换 display-mode，就得先 stub 再重挂。
 */
function restartWatch() {
  stopWatching?.()
  __resetInstallPromptForTests()
  stopWatching = watchInstallPrompt()
}

beforeEach(() => {
  // 下面断言的是中文原文：这一页搬进词表之后，这些句子仍然要一模一样。
  // （换成英文的例子在最后一个 describe 里。）
  setLocale('zh-CN')
  stubStandalone(false)
  stubUserAgent('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Safari/537.36')
  restartWatch()
})

afterEach(() => {
  stopWatching?.()
  stopWatching = null
  vi.unstubAllGlobals()
})

const mount = () => render(Page, { global: { plugins: [vuetify, i18n] } })

describe('浏览器愿意让我们问的时候', () => {
  it('给出安装按钮，点了就是把机会交给系统', async () => {
    const event = announceInstallOpportunity()
    const { getByText, container } = mount()

    const button = getByText('安装到这台设备')
    await fireEvent.click(button)
    // install() 是异步的（等 prompt() 和 userChoice），放一帧再断言。
    await new Promise((r) => setTimeout(r, 0))

    expect(event.prompt).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('已交给系统安装')
  })
})

describe('给不了按钮的时候', () => {
  it('Android / 桌面：写清 Chrome 菜单里点哪几下', () => {
    const { container, queryByText } = mount()

    expect(queryByText('安装到这台设备')).toBeNull()
    expect(container.textContent).toContain('Chrome')
    expect(container.textContent).toContain('安装应用')
  })

  it('iOS：走分享菜单，不写「⋮」', () => {
    stubUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15')
    const { container } = mount()

    expect(container.textContent).toContain('Safari')
    expect(container.textContent).toContain('添加到主屏幕')
    expect(container.textContent).not.toContain('安装应用')
  })

  it('「请用 Chrome 装」一直在——真机踩过的就是这一条', () => {
    const { container } = mount()
    expect(container.textContent).toContain('别用「添加到桌面」的国产浏览器')
    expect(container.textContent).toContain('发不了图')
  })
})

describe('已经装好了', () => {
  it('不再劝人安装，也不再画步骤', () => {
    stubStandalone(true)
    restartWatch()
    const { container, queryByText } = mount()

    expect(container.textContent).toContain('已经装好了')
    expect(queryByText('安装到这台设备')).toBeNull()
    expect(container.querySelector('.steps')).toBeNull()
  })
})

describe('换成英文', () => {
  it('整页没有一个汉字，步骤里的浏览器名还是加粗的', () => {
    setLocale('en')
    const { container } = mount()

    // 步骤第一句用的是 i18n-t 的 {browser} 插槽：写错了不会报错，只会把
    // `{browser}` 原样印出来。
    expect(container.textContent).not.toContain('{browser}')
    expect(container.querySelector('ol strong')?.textContent).toBe('Chrome')
    expect(container.textContent).toContain('Install with Chrome, not with')
    expect(container.textContent ?? '').not.toMatch(/[㐀-䶿一-鿿豈-﫿]/)
  })
})
