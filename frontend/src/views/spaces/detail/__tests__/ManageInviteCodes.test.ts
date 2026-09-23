import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listInviteCodes = vi.fn()
const createInviteCode = vi.fn()
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listInviteCodes: (...args: unknown[]) => listInviteCodes(...args),
    createInviteCode: (...args: unknown[]) => createInviteCode(...args),
  },
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { spaceId: '647' } }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import ManageInviteCodes from '../ManageInviteCodes.vue'

const CREATE = 'spaces.inviteCodes.create'

function mountPage() {
  return render(ManageInviteCodes, { global: { plugins: [createVuetify({ components, directives })] } })
}

function code(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    spaceId: 647,
    code: 'XB6SN6CH76',
    maxUses: 50,
    useCount: 0,
    expiresAt: null,
    createdAt: Date.UTC(2026, 8, 22),
    ...overrides,
  }
}

beforeEach(() => {
  listInviteCodes.mockReset().mockResolvedValue({ data: { inviteCodes: [code()] } })
  createInviteCode.mockReset().mockResolvedValue({ data: { inviteCode: code() } })
})

// Vuetify's overlay (the create dialog) reads both of these the moment it opens,
// and jsdom has neither.
beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterEach(cleanup)

describe('space invite codes', () => {
  it('asks the space it is standing in and lists what comes back', async () => {
    const page = mountPage()
    expect(await page.findByText('XB6SN6CH76')).toBeTruthy()
    expect(listInviteCodes).toHaveBeenCalledWith(647)
  })

  it('calls a used-up code used up, and a fresh one usable', async () => {
    listInviteCodes.mockResolvedValue({
      data: { inviteCodes: [code({ id: 1 }), code({ id: 2, code: 'USEDUP0000', useCount: 50 })] },
    })
    const page = mountPage()
    await page.findByText('USEDUP0000')
    expect(page.getAllByText('spaces.inviteCodes.statusUsable')).toHaveLength(1)
    expect(page.getAllByText('spaces.inviteCodes.statusExhausted')).toHaveLength(1)
  })

  it('offers the create button on an empty board instead of a dead end', async () => {
    listInviteCodes.mockResolvedValue({ data: { inviteCodes: [] } })
    const page = mountPage()
    expect(await page.findByText('spaces.inviteCodes.empty')).toBeTruthy()
    expect(page.getAllByRole('button', { name: CREATE }).length).toBeGreaterThan(0)
  })

  it('does not dress a failed request up as an empty board', async () => {
    listInviteCodes.mockRejectedValue(new Error('Forbidden'))
    const page = mountPage()
    await waitFor(() => expect(listInviteCodes).toHaveBeenCalled())
    expect(page.queryByText('spaces.inviteCodes.empty')).toBeNull()
  })

  it('sends the usage limit from the dialog and refetches the list', async () => {
    listInviteCodes.mockResolvedValue({ data: { inviteCodes: [] } })
    const page = mountPage()
    await page.findByText('spaces.inviteCodes.empty')

    await fireEvent.click(page.getAllByRole('button', { name: CREATE })[0])
    const usage = await page.findByLabelText('spaces.inviteCodes.maxUses')
    await fireEvent.update(usage, '5')

    const buttons = page.getAllByRole('button', { name: CREATE })
    await fireEvent.click(buttons[buttons.length - 1])

    await waitFor(() => expect(createInviteCode).toHaveBeenCalledWith(647, { maxUses: 5 }))
    expect(listInviteCodes).toHaveBeenCalledTimes(2)
  })

  it('refuses a usage limit that is not a positive whole number', async () => {
    listInviteCodes.mockResolvedValue({ data: { inviteCodes: [] } })
    const page = mountPage()
    await page.findByText('spaces.inviteCodes.empty')

    await fireEvent.click(page.getAllByRole('button', { name: CREATE })[0])
    const usage = await page.findByLabelText('spaces.inviteCodes.maxUses')
    await fireEvent.update(usage, '0')

    const buttons = page.getAllByRole('button', { name: CREATE })
    await fireEvent.click(buttons[buttons.length - 1])

    // 对话框不关，也一个请求都没发。
    await waitFor(() => expect(page.queryByLabelText('spaces.inviteCodes.maxUses')).not.toBeNull())
    expect(createInviteCode).not.toHaveBeenCalled()
  })
})
