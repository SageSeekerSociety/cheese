import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getJoinLink = vi.fn()
const resetJoinLink = vi.fn()
const updateJoinLink = vi.fn()
const update = vi.fn()
const copy = vi.fn()
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getJoinLink: (...args: unknown[]) => getJoinLink(...args),
    resetJoinLink: (...args: unknown[]) => resetJoinLink(...args),
    updateJoinLink: (...args: unknown[]) => updateJoinLink(...args),
    update: (...args: unknown[]) => update(...args),
  },
}))

import TeamJoinLinkCard from './TeamJoinLinkCard.vue'

import { setLocale } from '@/i18n'

const team = {
  id: 7,
  name: 'Cheese 核心组',
  intro: '',
  avatarId: 1,
  owner: { id: 1 },
  admins: { total: 0, examples: [] },
  members: { total: 0, examples: [] },
  role: 'OWNER',
  visibility: 'public',
} as unknown as Team

beforeAll(() => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } })
})

beforeEach(() => {
  setLocale('zh-CN')
  getJoinLink.mockReset().mockResolvedValue({ data: { token: 'first', approval: true } })
  resetJoinLink.mockReset().mockResolvedValue({ data: { token: 'second', approval: true } })
  updateJoinLink.mockReset().mockResolvedValue({ data: { token: 'first', approval: false } })
  update.mockReset().mockResolvedValue({ data: { team: { ...team, visibility: 'stealth' } } })
  copy.mockReset().mockResolvedValue(undefined)
})
afterEach(cleanup)

function mount() {
  return render(TeamJoinLinkCard as unknown as Component, {
    props: { team },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function linkValue() {
  return (screen.getAllByRole('textbox')[0] as HTMLInputElement).value
}

describe('managing how people get into a team', () => {
  it('hands out a link that can be copied', async () => {
    mount()
    await waitFor(() => expect(linkValue()).toBe(`${location.origin}/team-invites/first`))
    await fireEvent.click(screen.getByRole('button', { name: '复制链接' }))
    expect(copy).toHaveBeenCalledWith(`${location.origin}/team-invites/first`)
    await screen.findByRole('button', { name: '已复制' })
  })

  it('replaces the link when it is reset', async () => {
    mount()
    await waitFor(() => expect(linkValue()).toContain('/team-invites/first'))
    await fireEvent.click(screen.getByRole('button', { name: '重置链接' }))
    await waitFor(() => expect(linkValue()).toContain('/team-invites/second'))
    expect(resetJoinLink).toHaveBeenCalledWith(7)
  })

  it('turns approval off', async () => {
    mount()
    const approval = await screen.findByLabelText('加入需要审批')
    await waitFor(() => expect((approval as HTMLInputElement).disabled).toBe(false))
    expect((approval as HTMLInputElement).checked).toBe(true)
    ;(approval as HTMLInputElement).click()
    await waitFor(() => expect(updateJoinLink).toHaveBeenCalledWith(7, { approval: false }))
    await waitFor(() => expect((screen.getByLabelText('加入需要审批') as HTMLInputElement).checked).toBe(false))
  })

  it('hides the team from search and reports the saved team', async () => {
    const { emitted } = mount()
    const stealth = (await screen.findByLabelText(/^隐身/)) as HTMLInputElement
    await waitFor(() => expect(stealth.disabled).toBe(false))
    stealth.click()
    await waitFor(() => expect(update).toHaveBeenCalledWith(7, { visibility: 'stealth' }))
    await waitFor(() => expect(emitted().updated?.[0]).toEqual([{ ...team, visibility: 'stealth' }]))
  })

  it('says so when the link cannot be loaded', async () => {
    getJoinLink.mockRejectedValue(new Error('boom'))
    mount()
    await screen.findByText('操作失败，请重试')
    expect(screen.queryByRole('button', { name: '复制链接' })).toBeNull()
  })
})
