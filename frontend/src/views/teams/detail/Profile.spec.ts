import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const replace = vi.fn()
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRouter: () => ({ replace }),
}))
const update = vi.fn()
vi.mock('@/api/teams', () => ({ TeamsApi: { update: (...args: unknown[]) => update(...args) } }))
const createAvatar = vi.fn()
vi.mock('@/api/avatars', () => ({ createAvatar: (...args: unknown[]) => createAvatar(...args) }))
const patchQuery = vi.fn(async () => {})
vi.mock('@/query/client', async () => ({
  ...(await vi.importActual<typeof import('@/query/client')>('@/query/client')),
  patchQuery: (...args: unknown[]) => patchQuery(...(args as [])),
}))

import Profile from './Profile.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

function crew(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'crew',
    name: '知是开发组',
    intro: '',
    avatarId: 0,
    owner: { id: 1 },
    admins: { total: 0, examples: [] },
    members: { total: 0, examples: [] },
    visibility: 'public',
    role: 'OWNER',
    ...overrides,
  } as unknown as Team
}

function mount(team: Team = crew()) {
  setLocale('zh-CN')
  const injected = ref(team)
  render(Profile as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: injected },
    },
  })
  return injected
}

const save = () => fireEvent.click(screen.getByRole('button', { name: '保存' }))

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('the team profile', () => {
  it('an empty name is not sent, and is said on the name field', async () => {
    mount()
    await fireEvent.update(screen.getByLabelText('团队名称'), '   ')
    await save()
    await screen.findByText('填写团队名称')
    expect(update).not.toHaveBeenCalled()
  })

  it('a new avatar is uploaded first, and its id goes up with the other fields', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:avatar-preview')
    createAvatar.mockResolvedValue({ avatarId: 77 })
    update.mockResolvedValue({ team: crew({ name: '芝士社', avatarId: 77 }) })
    const team = mount()
    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社')
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await save()

    await waitFor(() => expect(createAvatar).toHaveBeenCalledWith(file))
    expect(update).toHaveBeenCalledWith(7, { name: '芝士社', intro: '', avatarId: 77 })
    await waitFor(() => expect(team.value.name).toBe('芝士社'))
  })

  // 只改名字、没碰头像时，交上去的那一份里不能有 avatarId，否则现成的头像会被清掉。
  it('without a new avatar no avatarId is sent, so the current one stays', async () => {
    update.mockResolvedValue({ team: crew({ name: '芝士社' }) })
    mount()
    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社')
    await save()

    await waitFor(() => expect(update).toHaveBeenCalledWith(7, { name: '芝士社', intro: '' }))
    expect(createAvatar).not.toHaveBeenCalled()
  })

  it('a taken name is said on the name field, and typing clears it', async () => {
    update.mockRejectedValue(new ApiError(409, 'taken', 'Conflict'))
    mount()
    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社')
    await save()
    await screen.findByText('这个名称已被占用，换一个试试')
    expect(screen.queryByText('保存失败，请稍后重试')).toBeNull()

    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社二号')
    await waitFor(() => expect(screen.queryByText('这个名称已被占用，换一个试试')).toBeNull())
  })

  it('a member who is not an owner or admin can read it but not change it', async () => {
    mount(crew({ role: 'MEMBER' }))
    expect(screen.getByText('只有团队所有者和管理员能修改资料')).toBeTruthy()
    expect((screen.getByLabelText('团队名称') as HTMLInputElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  })
})

describe('the team address', () => {
  it('a new address is saved on its own, and the page moves onto it', async () => {
    update.mockResolvedValue({ team: crew({ handle: 'zhishi' }) })
    const team = mount()
    await fireEvent.update(screen.getByLabelText('团队地址'), ' zhishi ')
    await save()

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'TeamsDetailProfile', params: { handle: 'zhishi' } })
    )
    expect(update).toHaveBeenCalledWith(7, { handle: 'zhishi' })
    expect(team.value.handle).toBe('zhishi')
  })

  it('an address someone else holds is said on the address field, and the page stays', async () => {
    update.mockRejectedValue(new ApiError(409, 'taken', 'Conflict'))
    mount()
    await fireEvent.update(screen.getByLabelText('团队地址'), 'taken-name')
    await save()

    await screen.findByText('这个地址已被占用')
    expect(screen.queryByText('这个名称已被占用，换一个试试')).toBeNull()
    expect(replace).not.toHaveBeenCalled()
  })
})
