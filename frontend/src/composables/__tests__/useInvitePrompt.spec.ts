// 第一次采纳之后的邀请提醒：只在真正第一次采纳后出来一次；关掉、刷新都不再出来；
// 早就采纳过的人不出来；新手引导还在带时等它走完；复制的是带自己邀请码的注册链接。
import type { MyInvites } from '@/types/invites'

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetInvitePromptForTests,
  noteAccepted,
  primeInvitePrompt,
  useInvitePrompt as useInvitePromptUnscoped,
} from '@/composables/useInvitePrompt'

import { getMyInvites } from '@/api/invites'
import { copyLink } from '@/commands/copy'

vi.mock('@/api/invites', async (actual) => ({
  ...(await actual<typeof import('@/api/invites')>()),
  getMyInvites: vi.fn(),
}))
vi.mock('@/commands/copy', () => ({ copyLink: vi.fn().mockResolvedValue(true) }))
vi.mock('@/me', () => ({ myHandle: () => 'linxiao' }))

const mine = (unlocked: boolean): MyInvites => ({
  code: 'K7Q2M9',
  max_uses: unlocked ? 4 : 1,
  use_count: 0,
  unlocked,
  unlock_uses: 3,
  invite_credits: 0,
  month_credits: 0,
  month_cap: 1500,
  invitees: [],
})

/** 采纳前问到的邀请码是 `before`，采纳之后是解锁了的。 */
async function acceptWith(before: boolean) {
  vi.mocked(getMyInvites).mockResolvedValueOnce(mine(before)).mockResolvedValue(mine(true))
  await primeInvitePrompt()
  await noteAccepted()
}

/** 每一个都像挂在一个应用外壳里；重新打开页面时，上一个外壳连同它的监听一起没了。 */
let shells: ReturnType<typeof effectScope>[] = []
function useInvitePrompt(blocked: () => boolean) {
  const shell = effectScope()
  shells.push(shell)
  return shell.run(() => useInvitePromptUnscoped(blocked))!
}

/** 像重新打开页面那样。 */
function reload() {
  for (const shell of shells) shell.stop()
  shells = []
  __resetInvitePromptForTests()
}

beforeEach(() => {
  localStorage.clear()
  reload()
})
afterEach(() => vi.clearAllMocks())

describe('第一次采纳之后的邀请提醒', () => {
  it('第一次采纳后出来', async () => {
    await acceptWith(false)
    const prompt = useInvitePrompt(() => false)
    await nextTick()
    expect(prompt.open.value).toBe(true)
  })

  it('早就采纳过的人不出来', async () => {
    await acceptWith(true)
    const prompt = useInvitePrompt(() => false)
    await nextTick()
    expect(prompt.open.value).toBe(false)
  })

  it('没有等他采纳的卡、没问过邀请码，就不出来', async () => {
    vi.mocked(getMyInvites).mockResolvedValue(mine(true))
    await noteAccepted()
    const prompt = useInvitePrompt(() => false)
    await nextTick()
    expect(prompt.open.value).toBe(false)
  })

  it('关掉之后不再出来，刷新页面、再采纳一次也不出来', async () => {
    await acceptWith(false)
    const prompt = useInvitePrompt(() => false)
    await nextTick()
    prompt.dismiss()
    expect(prompt.open.value).toBe(false)

    reload()
    await acceptWith(false)
    const again = useInvitePrompt(() => false)
    await nextTick()
    expect(again.open.value).toBe(false)
  })

  it('出来过就算提醒过：没点关掉就刷新，也不再出来', async () => {
    await acceptWith(false)
    useInvitePrompt(() => false)
    await nextTick()

    reload()
    const again = useInvitePrompt(() => false)
    await nextTick()
    expect(again.open.value).toBe(false)
  })

  it('新手引导还在带时先等着，走完再出来', async () => {
    await acceptWith(false)
    const touring = ref(true)
    const prompt = useInvitePrompt(() => touring.value)
    await nextTick()
    expect(prompt.open.value).toBe(false)
    touring.value = false
    await nextTick()
    expect(prompt.open.value).toBe(true)
  })

  it('复制的是带自己邀请码的注册链接，复制完收起', async () => {
    await acceptWith(false)
    const prompt = useInvitePrompt(() => false)
    await nextTick()
    await prompt.copySignupLink()
    expect(copyLink).toHaveBeenCalledWith(expect.stringContaining('/account/signup?code=K7Q2M9'))
    expect(prompt.open.value).toBe(false)
  })
})
