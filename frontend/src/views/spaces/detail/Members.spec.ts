// 成员页钉住的规则：
// 1. 管理员（含所有者）不在成员表里也要列出来 —— 名单是两份的并集。
// 2. 改角色、转让所有者只有所有者能做；转让要先确认，没确认之前一个请求都不发。
// 3. 加入链接：进来就读；重置、切换「加入需要批准」各发对应的那一条请求。
// 4. 加入申请：批准 / 拒绝发的是那一份申请的 id；批准之后成员表重读。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMembers = vi.fn()
const updateAdmin = vi.fn()
const getJoinLink = vi.fn()
const updateJoinLink = vi.fn()
const resetJoinLink = vi.fn()
const listJoinRequests = vi.fn()
const approveJoinRequest = vi.fn()
const rejectJoinRequest = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    listMembers: (...a: unknown[]) => listMembers(...a),
    updateAdmin: (...a: unknown[]) => updateAdmin(...a),
    getJoinLink: (...a: unknown[]) => getJoinLink(...a),
    updateJoinLink: (...a: unknown[]) => updateJoinLink(...a),
    resetJoinLink: (...a: unknown[]) => resetJoinLink(...a),
    listJoinRequests: (...a: unknown[]) => listJoinRequests(...a),
    approveJoinRequest: (...a: unknown[]) => approveJoinRequest(...a),
    rejectJoinRequest: (...a: unknown[]) => rejectJoinRequest(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key, locale: { value: 'zh-CN' } }) }
})

import Members from './Members.vue'

import DialogContainer from '@/components/common/DialogContainer.vue'
import { setLocale } from '@/i18n'
import { dialogs } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

// The confirm dialog's buttons are read by their Chinese labels below.
setLocale('zh-CN')

const SPACE_ID = 11
const OWNER = { id: 1, username: 'boss', nickname: '所有者甲' }
const ADMIN = { id: 2, username: 'ma', nickname: '管理员乙' }

const Page = defineComponent({ render: () => h('div', [h(RouterView), h(DialogContainer)]) })

async function mount(currentUserId: number) {
  AccountService.user = { id: currentUserId } as never
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore(pinia)
  store.currentSpaceId = SPACE_ID
  store.currentSpace = {
    id: SPACE_ID,
    name: '一个空间',
    admins: [
      { user: OWNER, role: 'OWNER' },
      { user: ADMIN, role: 'ADMIN' },
    ],
    // What the server says this reader may do: only the owner manages admins.
    viewerPermissions:
      currentUserId === OWNER.id
        ? ['view', 'edit_settings', 'manage_members', 'manage_admins', 'delete']
        : ['view', 'edit_settings', 'manage_members'],
  } as never

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/members', name: 'Members', component: Members as Component },
      // 页头「邮箱域名组」跳的设置入口；名字与 `router/spaces.ts` 一致。
      {
        path: '/spaces/:spaceId/manage/settings/domain-groups',
        name: 'SpacesDetailSettingsDomainGroups',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/members`)
  await router.isReady()
  render(Page, { global: { plugins: [pinia, createVuetify({ components, directives }), router] } })
  await waitFor(() => expect(listMembers).toHaveBeenCalled())
  return store
}

function rows(): string[] {
  return Array.from(document.querySelectorAll('tbody tr')).map((tr) => (tr.textContent || '').trim())
}

function rowOf(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('tbody tr')).find((tr) => tr.textContent?.includes(name))
  expect(row, `没找到 ${name} 那一行`).toBeTruthy()
  return row as HTMLElement
}

function button(base: HTMLElement | Document, text: string): HTMLButtonElement {
  const found = Array.from(base.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  expect(found, `没找到写着「${text}」的按钮`).toBeTruthy()
  return found as HTMLButtonElement
}

describe('空间成员页', () => {
  beforeEach(() => {
    if (!('visualViewport' in window)) {
      Object.defineProperty(window, 'visualViewport', {
        configurable: true,
        value: {
          height: 800,
          width: 600,
          offsetTop: 0,
          offsetLeft: 0,
          scale: 1,
          addEventListener: () => {},
          removeEventListener: () => {},
        },
      })
    }
    // 菜单一开 VOverlay 会算位置，happy-dom 没给 devicePixelRatio。
    vi.stubGlobal('devicePixelRatio', 1)
    listMembers.mockResolvedValue({
      members: [
        { userId: 3, joinedAt: 0, user: { id: 3, username: 'lin', nickname: '成员丙' } },
        { userId: 4, joinedAt: 0, user: { id: 4, username: 'zhou', nickname: '成员丁' } },
      ],
    })
    updateAdmin.mockResolvedValue({ space: null })
    getJoinLink.mockResolvedValue({ token: 'tok-1', approval: false })
    updateJoinLink.mockImplementation(async (_id: number, data: { approval: boolean }) => ({
      token: 'tok-1',
      approval: data.approval,
    }))
    resetJoinLink.mockResolvedValue({ token: 'tok-2', approval: false })
    listJoinRequests.mockResolvedValue({ requests: [] })
    approveJoinRequest.mockResolvedValue(true)
    rejectJoinRequest.mockResolvedValue(true)
  })

  afterEach(() => {
    dialogs.splice(0, dialogs.length)
    cleanup()
    AccountService.user = null
    vi.clearAllMocks()
  })

  it('管理员不在成员表里也会列出来', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))
    expect(rows().some((r) => r.includes('所有者甲'))).toBe(true)
    expect(rows().some((r) => r.includes('管理员乙'))).toBe(true)
  })

  it('不是所有者的人看不到改角色的按钮', async () => {
    await mount(ADMIN.id)
    await waitFor(() => expect(rows().length).toBe(4))
    const texts = Array.from(document.querySelectorAll('tbody button')).map((b) => b.textContent?.trim())
    expect(texts).not.toContain('spaces.members.transfer')
    expect(texts).not.toContain('spaces.members.makeAdmin')
  })

  it('转让所有者先问一句，确认之后才发请求', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))

    await fireEvent.click(button(rowOf('管理员乙'), 'spaces.members.transfer'))
    await waitFor(() => expect(document.body.textContent).toContain('spaces.members.confirmTransfer'))
    expect(updateAdmin).not.toHaveBeenCalled()

    await fireEvent.click(button(document, '确定'))
    await waitFor(() => expect(updateAdmin).toHaveBeenCalledWith(SPACE_ID, ADMIN.id, { role: 'OWNER' }))
  })

  it('页头的「邮箱域名组」把人送到设置里那一页', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))
    const domains = screen.getByRole('link', { name: 'spaces.members.inviteDomainGroups' })
    expect(domains.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/manage/settings/domain-groups`)
  })

  it('进来就读这个空间的加入链接，画出完整的地址', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(getJoinLink).toHaveBeenCalledWith(SPACE_ID))
    const field = await screen.findByRole('textbox', { name: '加入链接' })
    expect((field as HTMLInputElement).value).toBe(`${window.location.origin}/space-invites/tok-1`)
  })

  it('重置链接：发重置请求，地址换成新的那条', async () => {
    await mount(OWNER.id)
    await screen.findByRole('textbox', { name: '加入链接' })

    await fireEvent.click(screen.getByRole('button', { name: '重置链接' }))
    await waitFor(() => expect(resetJoinLink).toHaveBeenCalledWith(SPACE_ID))
    await waitFor(() =>
      expect((screen.getByRole('textbox', { name: '加入链接' }) as HTMLInputElement).value).toBe(
        `${window.location.origin}/space-invites/tok-2`
      )
    )
  })

  it('打开「加入需要批准」：发的是 approval: true，申请那一块出现', async () => {
    await mount(OWNER.id)
    await screen.findByRole('textbox', { name: '加入链接' })
    expect(screen.queryByText('spaces.members.requests.empty')).toBeNull()

    await fireEvent.input(screen.getByRole('checkbox', { name: '加入需要批准' }), { target: { checked: true } })
    await waitFor(() => expect(updateJoinLink).toHaveBeenCalledWith(SPACE_ID, { approval: true }))
    expect(await screen.findByText('spaces.members.requests.empty')).toBeTruthy()
  })

  it('批准一份申请：发的是那一份的 id，成员表跟着重读', async () => {
    getJoinLink.mockResolvedValue({ token: 'tok-1', approval: true })
    listJoinRequests.mockResolvedValueOnce({
      requests: [{ id: 31, userId: 21, user: { id: 21, username: 'wu', nickname: '申请人戊' }, createdAt: 0 }],
    })
    await mount(OWNER.id)
    await screen.findByText('申请人戊')
    const before = listMembers.mock.calls.length

    await fireEvent.click(screen.getByRole('button', { name: 'spaces.members.requests.approve' }))
    await waitFor(() => expect(approveJoinRequest).toHaveBeenCalledWith(SPACE_ID, 31))
    expect(rejectJoinRequest).not.toHaveBeenCalled()
    await waitFor(() => expect(listMembers.mock.calls.length).toBeGreaterThan(before))
    await waitFor(() => expect(screen.queryByText('申请人戊')).toBeNull())
  })

  it('拒绝一份申请：发的是那一份的 id，不批准', async () => {
    getJoinLink.mockResolvedValue({ token: 'tok-1', approval: true })
    listJoinRequests.mockResolvedValueOnce({
      requests: [{ id: 32, userId: 22, user: { id: 22, username: 'ji', nickname: '申请人己' }, createdAt: 0 }],
    })
    await mount(OWNER.id)
    await screen.findByText('申请人己')

    await fireEvent.click(screen.getByRole('button', { name: 'spaces.members.requests.reject' }))
    await waitFor(() => expect(rejectJoinRequest).toHaveBeenCalledWith(SPACE_ID, 32))
    expect(approveJoinRequest).not.toHaveBeenCalled()
  })

  it('每个角色旁都写着一句这个角色能做什么', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))
    expect(rowOf('所有者甲').textContent).toContain('spaces.members.role.ownerHint')
    expect(rowOf('管理员乙').textContent).toContain('spaces.members.role.adminHint')
    expect(rowOf('成员丙').textContent).toContain('spaces.members.role.memberHint')
    expect(rowOf('成员丁').textContent).toContain('spaces.members.role.memberHint')
  })

  it('读失败时成员那一块换成原因和一条重试，不再假装只有管理员', async () => {
    listMembers.mockRejectedValueOnce(new Error('HTTP 500 for /members'))
    await mount(OWNER.id)
    // 名单没读到就整块换成失败：屏幕上是原因和重试，不是「管理员一个人在」的假名单。
    // 标题经 useI18n（这里被 mock 成原样返回 key），重试按钮走全局 t（真 i18n）。
    await waitFor(() => expect(document.body.textContent).toContain('spaces.members.loadMembersFailed'))
    expect(document.body.textContent).toContain('HTTP 500 for /members')
    expect(rows().length).toBe(0)

    await fireEvent.click(button(document, '重试'))
    await waitFor(() => expect(listMembers).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(rows().length).toBe(4))
  })
})
