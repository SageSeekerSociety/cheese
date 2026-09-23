/** 协议实质变更后的重新同意（#1486）：有待同意的就拦住；同意后放行；不同意就退出登录。 */
import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { getPendingConsents, acceptDocuments, apiLogout, accountLogout, push } = vi.hoisted(() => ({
  getPendingConsents: vi.fn(),
  acceptDocuments: vi.fn(),
  apiLogout: vi.fn(),
  accountLogout: vi.fn(),
  push: vi.fn(),
}))

vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  const id = ref<number | undefined>(undefined)
  return { default: { logout: accountLogout }, currentUserId: id }
})
vi.mock('@/network/api/legal', () => ({ LegalApi: { getPendingConsents, acceptDocuments } }))
vi.mock('@/network/api/users', () => ({ UserApi: { logout: apiLogout } }))
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<object>('vue-router')),
  useRouter: () => ({ push }),
}))

import ConsentGate from './ConsentGate.vue'

import { setLocale } from '@/i18n'
import { currentUserId } from '@/services/account'

const TERMS = { document: 'terms', title: '用户协议', version: '2.0', effectiveDate: '2026-10-01' }

function mount() {
  return render(ConsentGate, {
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.clearAllMocks()
  ;(currentUserId as unknown as { value: number | undefined }).value = undefined
  getPendingConsents.mockResolvedValue({ data: { pending: [TERMS] } })
  acceptDocuments.mockResolvedValue({ data: { pending: [] } })
  apiLogout.mockResolvedValue({})
})

async function signIn(id: number) {
  ;(currentUserId as unknown as { value: number | undefined }).value = id
  await nextTick()
}

afterEach(() => vi.unstubAllGlobals())

describe('ConsentGate', () => {
  it('asks nobody while signed out', async () => {
    mount()
    await nextTick()
    expect(getPendingConsents).not.toHaveBeenCalled()
    expect(screen.queryByText('协议已更新')).toBeNull()
  })

  it('stops a signed-in person with pending documents, and lets them through once they agree', async () => {
    mount()
    await signIn(7)
    expect(await screen.findByText('协议已更新')).toBeTruthy()
    expect(screen.getByText('用户协议')).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: '同意并继续' }))

    expect(acceptDocuments).toHaveBeenCalledWith({ terms: '2.0' })
    // jsdom 不跑离场动画，关掉的弹窗节点还在；「放行」看的是遮罩不再生效。
    await waitFor(() => expect(document.querySelector('.v-overlay--active')).toBeNull())
  })

  it('shows nothing when nothing is pending', async () => {
    getPendingConsents.mockResolvedValue({ data: { pending: [] } })
    mount()
    await signIn(7)
    await waitFor(() => expect(getPendingConsents).toHaveBeenCalled())
    expect(screen.queryByText('协议已更新')).toBeNull()
  })

  it('disagreeing signs out on the server and locally, and goes to sign-in', async () => {
    mount()
    await signIn(7)
    await fireEvent.click(await screen.findByRole('button', { name: '不同意并退出登录' }))

    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'SignIn' }))
    expect(apiLogout).toHaveBeenCalled()
    expect(accountLogout).toHaveBeenCalled()
    expect(acceptDocuments).not.toHaveBeenCalled()
  })
})
