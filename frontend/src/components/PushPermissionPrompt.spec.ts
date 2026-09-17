// 问推送权限的那一刻。
//
// 浏览器的推送权限被拒一次之后基本问不了第二次（permission 变成 denied，再调
// requestPermission 直接返回 denied、不弹窗）。一次问坏，这个渠道对这个人就永久关
// 闭了 —— 所以「什么时候问」是这个组件的全部内容，而它值得被钉住。
//
// #1084 定的时机：等到这个人第一次真的遇到一轮跑过一分钟。那时他正等着结果，
// 「完成后通知你」是一句他当下就听得懂的话；首屏上问，他还不知道为什么要授权。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import PushPermissionPrompt from './PushPermissionPrompt.vue'

const enablePush = vi.fn(() => Promise.resolve(true))
const pushAvailable = vi.fn(() => Promise.resolve(true))
const pushSupported = vi.fn(() => true)
const permissionSettled = vi.fn(() => false)

vi.mock('@/services/webPush', () => ({
  enablePush: () => enablePush(),
  pushAvailable: () => pushAvailable(),
  pushSupported: () => pushSupported(),
  permissionSettled: () => permissionSettled(),
}))

const Prompt = PushPermissionPrompt as unknown as Component
const ASKED_KEY = 'cheese:push-asked'
const ASK_TEXT = '本轮运行时间可能较长'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  localStorage.removeItem(ASKED_KEY)
  enablePush.mockClear()
  pushAvailable.mockClear()
  pushAvailable.mockResolvedValue(true)
  pushSupported.mockReturnValue(true)
  permissionSettled.mockReturnValue(false)
})

afterEach(() => {
  vi.useRealTimers()
})

function mountPrompt(working = true) {
  return render(Prompt, { props: { working }, global: { plugins: [vuetify] } })
}

/** 推进假时钟，并给那几个 await 落地的机会。 */
async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
  for (let i = 0; i < 12; i += 1) await Promise.resolve()
}

describe('问推送权限的时机', () => {
  it('一轮刚开始不问 —— 绝大多数轮次几秒就完了', async () => {
    const screen = mountPrompt()

    await advance(59_000)

    expect(screen.queryByText(ASK_TEXT, { exact: false })).toBeNull()
  })

  it('这一轮跑过一分钟才问', async () => {
    const screen = mountPrompt()

    await advance(61_000)

    expect(screen.queryByText(ASK_TEXT, { exact: false })).not.toBeNull()
  })

  it('一分钟之内就收工的轮次不问', async () => {
    const screen = mountPrompt()

    await advance(30_000)
    await screen.rerender({ working: false })
    await advance(120_000)

    expect(screen.queryByText(ASK_TEXT, { exact: false })).toBeNull()
  })

  it('说过「暂不开启」就不再问 —— 追问是人关掉一个渠道的头号原因', async () => {
    const first = mountPrompt()
    await advance(61_000)
    ;(first.getByText('暂不开启') as HTMLElement).click()
    await advance(0)
    expect(first.queryByText(ASK_TEXT, { exact: false })).toBeNull()
    first.unmount()

    const second = mountPrompt()
    await advance(61_000)

    expect(second.queryByText(ASK_TEXT, { exact: false })).toBeNull()
  })

  it('点了「开启通知」就去订阅，并记下问过了', async () => {
    const screen = mountPrompt()
    await advance(61_000)
    ;(screen.getByText('开启通知') as HTMLElement).click()
    await advance(0)

    expect(enablePush).toHaveBeenCalled()
    expect(localStorage.getItem(ASKED_KEY)).toBe('1')
    expect(screen.queryByText(ASK_TEXT, { exact: false })).toBeNull()
  })

  it('浏览器已经答复过权限就不再问', async () => {
    permissionSettled.mockReturnValue(true)
    const screen = mountPrompt()

    await advance(61_000)

    expect(screen.queryByText(ASK_TEXT, { exact: false })).toBeNull()
    // 连「这个部署开没开推送」都不该去问 —— 那是一次没有用处的请求。
    expect(pushAvailable).not.toHaveBeenCalled()
  })

  it('这个部署没开推送就不问 —— 问了也没有东西能发', async () => {
    pushAvailable.mockResolvedValue(false)
    const screen = mountPrompt()

    await advance(61_000)

    expect(screen.queryByText(ASK_TEXT, { exact: false })).toBeNull()
  })
})
