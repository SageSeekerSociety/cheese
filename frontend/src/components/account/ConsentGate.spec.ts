/** 协议实质变更后的重新同意（#1486）：有待同意的就拦住；同意后放行；不同意就退出登录。 */
import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { getPendingConsents, acceptDocuments, apiLogout, accountLogout } = vi.hoisted(() => ({
  getPendingConsents: vi.fn(),
  acceptDocuments: vi.fn(),
  apiLogout: vi.fn(),
  accountLogout: vi.fn(),
}))

vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  const id = ref<number | undefined>(undefined)
  return { default: { logout: accountLogout }, currentUserId: id }
})
vi.mock('@/network/api/legal', () => ({ LegalApi: { getPendingConsents, acceptDocuments } }))
vi.mock('@/network/api/users', () => ({ UserApi: { logout: apiLogout } }))

import ConsentGate from './ConsentGate.vue'

import { setLocale } from '@/i18n'
import { currentUserId } from '@/services/account'

const TERMS = { document: 'terms', title: '用户协议', version: '2.0', effectiveDate: '2026-10-01' }

const blank = defineComponent({ setup: () => () => h('div') })

/** 协议那两条公开页（`router/legal.ts`）：弹窗里点协议名要真的去得了那一页。
 *  登录页是不同意那条路的去处 —— 组件跳转走 `composables/useNavigation`，路由从
 *  应用上拿，所以装的必须是真路由（它读的就是这一个）。 */
function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/:any(.*)*', component: blank },
    ],
  })
  return {
    router,
    ...render(ConsentGate, {
      global: { plugins: [createVuetify({ components, directives }), router] },
    }),
  }
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

    // 弹窗里那几个协议名就是去看那份协议的路：新开一页（`target="_blank"`），不能
    // 把已经登进来的这一页顶掉 —— 它自己被这个弹窗盖着，走了就回不来。
    const terms = screen.getByText('用户协议')
    expect(terms.getAttribute('href')).toBe('/legal/terms')
    expect(terms.getAttribute('target')).toBe('_blank')

    await fireEvent.click(screen.getByRole('button', { name: '同意并继续' }))

    expect(acceptDocuments).toHaveBeenCalledWith({ terms: '2.0' })
    // jsdom 不跑离场动画，关掉的弹窗节点还在；「放行」看的是遮罩不再生效。
    await waitFor(() => expect(document.querySelector('.v-overlay--active')).toBeNull())
  })

  it('each document name goes to its own page, not all to the same one', async () => {
    getPendingConsents.mockResolvedValue({
      data: { pending: [TERMS, { ...TERMS, document: 'privacy', title: '隐私政策' }] },
    })
    mount()
    await signIn(7)

    expect((await screen.findByText('隐私政策')).getAttribute('href')).toBe('/legal/privacy')
    expect(screen.getByText('用户协议').getAttribute('href')).toBe('/legal/terms')

    // 收摊前把弹窗关掉：别的那几条要么本来就没弹窗，要么自己关掉了，而 VOverlay
    // 卸载时还要再读一次 visualViewport —— 那时 `afterEach` 已经把它撤了。
    await fireEvent.click(screen.getByRole('button', { name: '同意并继续' }))
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
    const { router } = mount()
    await signIn(7)
    await fireEvent.click(await screen.findByRole('button', { name: '不同意并退出登录' }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'))
    expect(apiLogout).toHaveBeenCalled()
    expect(accountLogout).toHaveBeenCalled()
    expect(acceptDocuments).not.toHaveBeenCalled()
  })
})
