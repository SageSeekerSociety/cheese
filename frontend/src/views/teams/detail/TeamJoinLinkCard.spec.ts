import type { Component } from 'vue'
import type { Team } from '@/types'

import { reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import TeamJoinLinkCard from './TeamJoinLinkCard.vue'

import { setLocale } from '@/i18n'

function makeTeam() {
  return reactive({
    id: 7,
    handle: 'cheese-core',
    name: 'Cheese 核心组',
    intro: '',
    avatarId: 1,
    owner: { id: 1 },
    admins: { total: 0, examples: [] },
    members: { total: 0, examples: [] },
    role: 'OWNER',
    visibility: 'public',
  }) as unknown as Team
}

/** 这一件只吃数据、往上报动作：摆什么由 props 说了算，写什么靠下面那几条断言。 */
function mount(
  overrides: Partial<{
    team: Team
    link: { token: string; approval: boolean } | null
    busy: boolean
    error: string
    copied: boolean
  }> = {}
) {
  const team = overrides.team ?? makeTeam()
  return render(TeamJoinLinkCard as unknown as Component, {
    props: {
      link: { token: 'first', approval: true },
      busy: false,
      error: '',
      copied: false,
      ...overrides,
      team,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function linkFieldValue() {
  return (screen.getByLabelText('团队链接') as HTMLInputElement).value
}

beforeAll(() => {
  setLocale('zh-CN')
})
afterEach(cleanup)

describe('what it draws', () => {
  it('draws the link, the approval switch and who can find the team', async () => {
    mount()

    expect(linkFieldValue()).toBe(`${location.origin}/team-invites/first`)
    expect((screen.getByLabelText('加入需要审批') as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/^公开/) as HTMLInputElement).checked).toBe(true)
  })

  it('leaves the link out until one has been read back', () => {
    mount({ link: null })

    expect(screen.queryByRole('button', { name: '复制链接' })).toBeNull()
    expect(screen.queryByLabelText('团队链接')).toBeNull()
  })

  it('says why the last thing did not go through', () => {
    mount({ error: '操作失败，请重试' })

    expect(screen.getByText('操作失败，请重试')).toBeTruthy()
  })

  it('says the link was copied when the page says so', () => {
    mount({ copied: true })

    expect(screen.getByRole('button', { name: '已复制' })).toBeTruthy()
  })

  it('has nothing to click while something is going on', () => {
    mount({ busy: true })

    expect((screen.getByRole('button', { name: '复制链接' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '重置链接' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('加入需要审批') as HTMLInputElement).disabled).toBe(true)
  })
})

describe('what it asks the page to do', () => {
  it('asks for the link to be copied', async () => {
    const { emitted } = mount()

    await fireEvent.click(screen.getByRole('button', { name: '复制链接' }))

    expect(emitted('copy')).toEqual([[`${location.origin}/team-invites/first`]])
  })

  it('asks for a new link to be handed out', async () => {
    const { emitted } = mount()

    await fireEvent.click(screen.getByRole('button', { name: '重置链接' }))

    expect(emitted('reset')).toEqual([[]])
  })

  it('asks for approval to be turned off', async () => {
    const { emitted } = mount()

    ;(screen.getByLabelText('加入需要审批') as HTMLInputElement).click()

    expect(emitted('setApproval')).toEqual([[false]])
  })

  it('asks for the team to be hidden from search', async () => {
    const { emitted } = mount()

    ;(screen.getByLabelText(/^隐身/) as HTMLInputElement).click()

    expect(emitted('setVisibility')).toEqual([['stealth']])
  })
})
