// 成员页钉住的规则：
// 1. 管理员（含所有者）不在成员表里也要列出来 —— 名单是两份的并集。
// 2. 「加入方式」有记录就写那张码，没记录写「未知」，不拿别的码顶上。
// 3. 改角色、转让所有者只有所有者能做；转让要先确认，没确认之前一个请求都不发。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMembers = vi.fn()
const updateAdmin = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listMembers: (...a: unknown[]) => listMembers(...a),
    updateAdmin: (...a: unknown[]) => updateAdmin(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
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
  } as never

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/members', name: 'Members', component: Members as Component },
      // 页头「邀请成员」跳的两处设置入口；名字与 `router/spaces.ts` 一致。
      {
        path: '/spaces/:spaceId/manage/settings/invite-codes',
        name: 'SpacesDetailSettingsInviteCodes',
        component: { template: '<div />' },
      },
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
      data: {
        members: [
          { userId: 3, joinedAt: 0, user: { id: 3, username: 'lin', nickname: '成员丙' }, inviteCode: null },
          {
            userId: 4,
            joinedAt: 0,
            user: { id: 4, username: 'zhou', nickname: '成员丁' },
            inviteCode: { id: 9, code: 'ABCD2345EF' },
          },
        ],
      },
    })
    updateAdmin.mockResolvedValue({ data: { space: null } })
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

  it('有记录就写那张码，没记录写未知', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))
    expect(rowOf('成员丁').textContent).toContain('ABCD2345EF')
    expect(rowOf('成员丙').textContent).toContain('spaces.members.unknown')
    expect(rowOf('成员丙').textContent).not.toContain('ABCD2345EF')
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

  it('页头的「邀请成员」把人送到设置里那两处邀请入口', async () => {
    await mount(OWNER.id)
    await waitFor(() => expect(rows().length).toBe(4))

    await fireEvent.click(button(document, 'spaces.members.invite'))
    await waitFor(() => expect(document.body.textContent).toContain('spaces.members.inviteCodes'))

    const links = Array.from(document.querySelectorAll('a'))
    const codes = links.find((a) => a.textContent?.includes('spaces.members.inviteCodes'))
    const domains = links.find((a) => a.textContent?.includes('spaces.members.inviteDomainGroups'))
    expect(codes?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/manage/settings/invite-codes`)
    expect(domains?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/manage/settings/domain-groups`)
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
