// 「知是正在内测」：每个账号在这台浏览器上说一次；关掉、刷新都不再出来；别的弹窗、
// 新手引导在的时候先等；数拿不到就不出来；领取成功就关上，没领到留着。
import type { BetaCredits } from '@/types/creditOffers'

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useBetaNotice as useBetaNoticeUnscoped } from '@/composables/useBetaNotice'

import { claimBetaCredits, getBetaCredits } from '@/api/creditOffers'

vi.mock('@/api/creditOffers', async (actual) => ({
  ...(await actual<typeof import('@/api/creditOffers')>()),
  getBetaCredits: vi.fn(),
  claimBetaCredits: vi.fn(),
}))
vi.mock('vuetify-sonner', () => ({ toast: vi.fn() }))
const handle = ref('linxiao')
vi.mock('@/me', () => ({ myHandle: () => handle.value }))

const offer = (claimed: boolean): BetaCredits => ({
  credits: 500,
  claimed,
  feedback_credits: 100,
  plan_credits: 1000,
  invite_credits: 150,
  invitee_credits: 150,
})

/** 每一个都像挂在一个应用外壳里；重新打开页面时，上一个外壳连同它的监听一起没了。 */
let shells: ReturnType<typeof effectScope>[] = []
function useBetaNotice(blocked: () => boolean) {
  const shell = effectScope()
  shells.push(shell)
  return shell.run(() => useBetaNoticeUnscoped(blocked))!
}
function reload() {
  for (const shell of shells) shell.stop()
  shells = []
}
const settle = () => new Promise((resolve) => setTimeout(resolve))

beforeEach(() => {
  localStorage.clear()
  handle.value = 'linxiao'
  reload()
  vi.mocked(getBetaCredits).mockResolvedValue(offer(false))
})
afterEach(() => vi.clearAllMocks())

describe('「知是正在内测」说明', () => {
  it('第一次打开应用时出来，带着接口给的数', async () => {
    const notice = useBetaNotice(() => false)
    await settle()
    expect(notice.open.value).toBe(true)
    expect(notice.offer.value?.plan_credits).toBe(1000)
  })

  it('关掉之后刷新页面，不再出来', async () => {
    const first = useBetaNotice(() => false)
    await settle()
    first.dismiss()
    reload()
    const again = useBetaNotice(() => false)
    await settle()
    expect(again.open.value).toBe(false)
    expect(getBetaCredits).toHaveBeenCalledTimes(1)
  })

  it('出来过一次就算说过了，哪怕没点任何按钮就刷新了', async () => {
    useBetaNotice(() => false)
    await settle()
    reload()
    const again = useBetaNotice(() => false)
    await settle()
    expect(again.open.value).toBe(false)
  })

  it('换了一个账号，那个账号还要说一次', async () => {
    useBetaNotice(() => false)
    await settle()
    reload()
    handle.value = 'zhouming'
    const other = useBetaNotice(() => false)
    await settle()
    expect(other.open.value).toBe(true)
  })

  it('别的弹窗或新手引导在的时候先等，等它们不在了再出来', async () => {
    const busy = ref(true)
    const notice = useBetaNotice(() => busy.value)
    await settle()
    expect(notice.open.value).toBe(false)
    expect(getBetaCredits).not.toHaveBeenCalled()
    busy.value = false
    await settle()
    expect(notice.open.value).toBe(true)
  })

  it('数拿不到就不出来，也不记成说过了：下次打开再试', async () => {
    vi.mocked(getBetaCredits).mockRejectedValueOnce(new Error('offline'))
    const notice = useBetaNotice(() => false)
    await settle()
    expect(notice.open.value).toBe(false)
    reload()
    const again = useBetaNotice(() => false)
    await settle()
    expect(again.open.value).toBe(true)
  })

  it('领取成功就关上', async () => {
    vi.mocked(claimBetaCredits).mockResolvedValue({ credits: 500, issued: true })
    const notice = useBetaNotice(() => false)
    await settle()
    expect(await notice.claim()).toBe(true)
    expect(claimBetaCredits).toHaveBeenCalledTimes(1)
    expect(notice.open.value).toBe(false)
  })

  it('没领到就留着，说一句没领到', async () => {
    vi.mocked(claimBetaCredits).mockRejectedValue(new Error('500'))
    const notice = useBetaNotice(() => false)
    await settle()
    expect(await notice.claim()).toBe(false)
    expect(notice.open.value).toBe(true)
    expect(notice.claimFailed.value).toBe(true)
  })

  it('没登录的时候不出来', async () => {
    handle.value = ''
    const notice = useBetaNotice(() => false)
    await nextTick()
    await settle()
    expect(notice.open.value).toBe(false)
    expect(getBetaCredits).not.toHaveBeenCalled()
  })
})
