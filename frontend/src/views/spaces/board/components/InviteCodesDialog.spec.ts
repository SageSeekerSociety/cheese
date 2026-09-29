// 邀请码弹窗：改得动、收得回，而且**改完立刻见效**。
//
// 这四条钉的都是「界面上看到的」而不是「接口被调了」：改完要用服务端回来的那一版重画
// （不是本地猜一个新值），撤销点两下才发请求，人数调到比已用数还少在本地就说清楚 ——
// 后端也挡（400），但让人先看见原因比等一个红 toast 好。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// 弹窗是一个 VOverlay，而测试环境没有 visualViewport：不补上根本挂不起来，
// 测到的就成了「点开什么也没有」。
beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())

const listInviteCodes = vi.fn()
const updateInviteCode = vi.fn()
const revokeInviteCode = vi.fn()
const createInviteCode = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listInviteCodes: (...a: unknown[]) => listInviteCodes(...a),
    updateInviteCode: (...a: unknown[]) => updateInviteCode(...a),
    revokeInviteCode: (...a: unknown[]) => revokeInviteCode(...a),
    createInviteCode: (...a: unknown[]) => createInviteCode(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'vuetify-sonner'

import InviteCodesDialog from './InviteCodesDialog.vue'

const SPACE_ID = 11

function code(over: Record<string, unknown> = {}) {
  return {
    id: 7,
    spaceId: SPACE_ID,
    code: 'ABCD2345EF',
    maxUses: 1,
    useCount: 1,
    expiresAt: null,
    createdAt: Date.now(),
    ...over,
  }
}

/** 挂起来并等到某一串出现在屏幕上 —— 用例各自关心的那串不一样，
 *  钉不住「加载完了」就只在等一个超时。 */
async function mount(expectText = 'ABCD2345EF') {
  const utils = render(InviteCodesDialog, {
    props: { spaceId: SPACE_ID, modelValue: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await waitFor(() => expect(document.body.textContent).toContain(expectText))
  return utils
}

describe('邀请码弹窗', () => {
  beforeEach(() => {
    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [code()] } }))
    updateInviteCode.mockImplementation(async () => ({ data: { inviteCode: code() } }))
    revokeInviteCode.mockImplementation(async () => ({ data: {} }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('改用量之后按服务端回来的那一版重画', async () => {
    await mount()
    expect(document.body.textContent).toContain('已用尽')

    // 服务端在 PATCH 之后会把这行换成新的：1/3、还可使用。
    listInviteCodes.mockImplementation(async () => ({
      data: { inviteCodes: [code({ maxUses: 3 })] },
    }))

    await fireEvent.click(screen.getByText('调整'))
    await fireEvent.update(screen.getByLabelText('可用人数'), '3')
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(updateInviteCode).toHaveBeenCalledTimes(1))
    // 说明这一格没人碰过 —— 表单带着它原来的值（这里是没有），所以发出去的是 null。
    expect(updateInviteCode).toHaveBeenCalledWith(SPACE_ID, 7, {
      maxUses: 3,
      expiresAt: null,
      note: null,
    })

    // 重画是拿回来的那份，不是本地改出来的数字：状态也跟着从「已用尽」变回「可用」。
    await waitFor(() => expect(document.body.textContent).toContain('1 / 3 人已用'))
    expect(document.body.textContent).toContain('可用')
    expect(listInviteCodes).toHaveBeenCalledTimes(2)
  })

  it('把期限清空 = 永不过期，发的是显式的 null 而不是不发', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: { inviteCodes: [code({ expiresAt: Date.now() + 86_400_000 })] },
    }))
    await mount()
    expect(document.body.textContent).toContain('有效期至')

    // 清空之后服务端再报回来的就是一份不过期的码。
    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [code()] } }))

    await fireEvent.click(screen.getByText('调整'))
    expect(screen.getByLabelText('有效期至（留空 = 永不过期）')).toBeTruthy()
    await fireEvent.update(screen.getByLabelText('有效期至（留空 = 永不过期）'), '')
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(updateInviteCode).toHaveBeenCalledTimes(1))
    // 「不发」在后端读作「别动这一项」，所以清空必须真的发一个 null 出去。
    expect(updateInviteCode.mock.calls[0][2]).toEqual({ maxUses: 1, expiresAt: null, note: null })
    await waitFor(() => expect(document.body.textContent).toContain('永不过期'))
  })

  it('给一个永不过期的码加上期限：取的是那一天的最后一刻', async () => {
    await mount()

    await fireEvent.click(screen.getByText('调整'))
    await fireEvent.update(screen.getByLabelText('有效期至（留空 = 永不过期）'), '2026-12-31')
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(updateInviteCode).toHaveBeenCalledTimes(1))
    const sent = updateInviteCode.mock.calls[0][2] as { expiresAt: number }
    // 选「今天」不该当场就过期，所以取的是那一天的终点而不是零点。
    // 按本机时区算出来比对，不写死 UTC 串 —— 那个日期框给的是本地的「12月31日」，
    // 换台时区不对的机器跑就会显得像实现错了。
    expect(sent.expiresAt).toBe(new Date(2026, 11, 31, 23, 59, 59, 999).getTime())
  })

  it('撤销要点两下，第一下不发请求', async () => {
    await mount()

    await fireEvent.click(screen.getByText('撤销'))
    expect(revokeInviteCode).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText('确认撤销')).toBeTruthy())

    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [] } }))
    await fireEvent.click(screen.getByText('确认撤销'))

    await waitFor(() => expect(revokeInviteCode).toHaveBeenCalledWith(SPACE_ID, 7))
    await waitFor(() => expect(document.body.textContent).not.toContain('ABCD2345EF'))
  })

  it('人数改到比已用掉的还少，本地就拦住并说明原因', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: { inviteCodes: [code({ maxUses: 5, useCount: 3 })] },
    }))
    await mount()

    await fireEvent.click(screen.getByText('调整'))
    await fireEvent.update(screen.getByLabelText('可用人数'), '2')
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('可用人数不能少于已经用掉的 3 人'))
    expect(updateInviteCode).not.toHaveBeenCalled()
  })
})

describe('这一张码是谁的、给谁用的', () => {
  beforeEach(() => {
    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [code()] } }))
    updateInviteCode.mockImplementation(async () => ({ data: { inviteCode: code() } }))
    createInviteCode.mockImplementation(async () => ({ data: { inviteCode: code({ id: 8 }) } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('建码人与说明都摆出来，不藏在只有点开才看得到的地方', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: {
        inviteCodes: [
          code({
            createdBy: { id: 3, username: 'lin', nickname: '林' },
            note: '给新来的设计师',
          }),
        ],
      },
    }))
    await mount()

    expect(document.body.textContent).toMatch(/建码人\s*@林/)
    expect(document.body.textContent).toContain('给新来的设计师')
    // 昵称为空时退到用户名，这是后端 `createdBy` 里两个都可能缺的那一格的兜底。
    expect(document.body.textContent).not.toContain('未知')
  })

  it('两处都缺的时候写「未知」「没写说明」，不留白', async () => {
    // 老码两样都没有：`created_by` 空、`note` 空，这在库里都是一条真话，不是坏掉。
    await mount()

    expect(document.body.textContent).toContain('建码人 未知')
    expect(document.body.textContent).toContain('没写说明')
  })

  it('调整时预填着原来的说明，保存原样发回去', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: { inviteCodes: [code({ note: '给新来的设计师' })] },
    }))
    await mount()

    await fireEvent.click(screen.getByText('调整'))
    const field = screen.getByLabelText('说明（留空 = 清掉）') as HTMLInputElement
    expect(field.value).toBe('给新来的设计师')

    await fireEvent.update(field, '给新来的设计师（小周）')
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(updateInviteCode).toHaveBeenCalledTimes(1))
    expect(updateInviteCode.mock.calls[0][2]).toMatchObject({ note: '给新来的设计师（小周）' })
  })

  it('把说明清空 = 真的清掉，发的是显式的 null', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: { inviteCodes: [code({ note: '给新来的设计师' })] },
    }))
    await mount()

    await fireEvent.click(screen.getByText('调整'))
    await fireEvent.update(screen.getByLabelText('说明（留空 = 清掉）'), '   ')
    // 清掉之后服务端再报回来的就是一份没有说明的码。
    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [code()] } }))
    await fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(updateInviteCode).toHaveBeenCalledTimes(1))
    expect(updateInviteCode.mock.calls[0][2]).toMatchObject({ note: null })
    await waitFor(() => expect(document.body.textContent).toContain('没写说明'))
  })

  it('新建时写的说明跟着建码请求一起发；全是空白 = 没有说明', async () => {
    await mount()

    await fireEvent.click(screen.getByText('新建'))
    await fireEvent.update(screen.getByLabelText('说明（这张码给谁 / 干什么用，可不填）'), '  发 布 会  ')
    await fireEvent.click(screen.getByText('生成'))

    await waitFor(() => expect(createInviteCode).toHaveBeenCalledTimes(1))
    expect(createInviteCode.mock.calls[0][1]).toMatchObject({ note: '发 布 会' })
  })

  it('一张什么都不填的新码发出去的是 null，不是一个空串', async () => {
    await mount()

    await fireEvent.click(screen.getByText('新建'))
    await fireEvent.click(screen.getByText('生成'))

    await waitFor(() => expect(createInviteCode).toHaveBeenCalledTimes(1))
    expect(createInviteCode.mock.calls[0][1]).toMatchObject({ note: null })
  })
})

describe('当前使用中的码', () => {
  beforeEach(() => {
    updateInviteCode.mockImplementation(async () => ({ data: { inviteCode: code() } }))
    revokeInviteCode.mockImplementation(async () => ({ data: {} }))
    createInviteCode.mockImplementation(async () => ({ data: { inviteCode: code({ id: 8 }) } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  /** 当前码那一块单独取出来看 —— 列表里也有同样的码串，只看整页文本会看串。 */
  function currentBlock(): HTMLElement {
    const el = document.querySelector('.current')
    if (!el) throw new Error('当前使用中的码那一块没渲染出来')
    return el as HTMLElement
  }

  it('拿的是还能用的那一张，不是列表第一格', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: {
        inviteCodes: [
          // 第一格就是用尽的 —— 照着第一格发给下一个人是发不出去的。
          code({ id: 7, code: 'USED1111AA', maxUses: 1, useCount: 1 }),
          code({ id: 8, code: 'LIVE2222BB', maxUses: 5, useCount: 0 }),
        ],
      },
    }))
    await mount('LIVE2222BB')

    expect(currentBlock().querySelector('.current__code')?.textContent).toContain('LIVE2222BB')
    expect(currentBlock().textContent).toContain('0 / 5 人已用')
  })

  it('全是用尽或过期的，就说没有可用的码，并说清楚该怎么办', async () => {
    listInviteCodes.mockImplementation(async () => ({
      data: {
        inviteCodes: [
          code({ id: 7, code: 'USED1111AA', maxUses: 1, useCount: 1 }),
          code({
            id: 8,
            code: 'DEAD2222BB',
            maxUses: 5,
            useCount: 0,
            expiresAt: Date.now() - 1000,
          }),
        ],
      },
    }))
    await mount('DEAD2222BB')

    expect(currentBlock().querySelector('.current__code')).toBeNull()
    expect(currentBlock().textContent).toContain('没有可用的码')
    expect(currentBlock().textContent).toContain('新建一个')
  })

  it('一张码都没有时也不留白', async () => {
    listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [] } }))
    render(InviteCodesDialog, {
      props: { spaceId: SPACE_ID, modelValue: true },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await waitFor(() => expect(document.body.textContent).toContain('没有可用的码'))
    expect(currentBlock().textContent).toContain('新建一个')
  })
})
