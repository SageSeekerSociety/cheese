// English mode must not leak Chinese on the challenge pages: each block here renders a
// piece of the task flow in `en` and checks the whole document for CJK characters.
// Fixture data is Latin-only, so any CJK found came from the component's own copy.
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'TipTapEditorStub',
      setup(_, { expose }) {
        expose({ editor: { getText: () => 'Body.' } })
        return () => h('div', { class: 'tiptap-editor' })
      },
    }),
  }
})

vi.mock('@/api', () => ({ listMyDevices: async () => ({ devices: [] }) }))
vi.mock('@/api/machineSkills', () => ({
  fetchSkillMarket: async () => ({ skills: [], plugins: [], plugin_total: 0, mcp: [], notes: [] }),
  fetchMachineSkill: async () => ({}),
}))

import LeaveTeamDialog from './components/LeaveTeamDialog.vue'
import PrivacyProtectionInfo from './components/PrivacyProtectionInfo.vue'
import TaskEligibilityAlerts from './components/TaskEligibilityAlerts.vue'
import TeamSelectionDialog from './components/TeamSelectionDialog.vue'
import InsightsView from './detail/InsightsView.vue'

import TaskForm from '@/components/tasks/TaskForm.vue'
import VerifyInfoForm from '@/components/tasks/VerifyInfoForm.vue'
import i18n, { setLocale } from '@/i18n'
import MarketView from '@/views/MarketView.vue'

const CJK = new RegExp('[\\u3400-\\u9fff\\uff00-\\uffef\\u3000-\\u303f]')

const plugins = () => [createVuetify({ components, directives }), i18n]

function cjkIn(text: string | null | undefined): string[] {
  return [...(text ?? '').matchAll(new RegExp(CJK, 'g'))].map((m) => m[0])
}

/** Visible text plus the attributes a reader or screen reader gets. */
function pageText(): string {
  const attrs = Array.from(document.body.querySelectorAll('[placeholder],[aria-label],[title],[label]'))
    .flatMap((el) => ['placeholder', 'aria-label', 'title', 'label'].map((a) => el.getAttribute(a) ?? ''))
    .join(' ')
  return `${document.body.textContent ?? ''} ${attrs}`
}

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

beforeEach(() => setLocale('en'))
afterEach(cleanup)
afterAll(() => setLocale('zh-CN'))

const TASK = {
  id: 1,
  name: 'Trace a segfault',
  intro: 'Use gdb',
  approved: 'APPROVED',
  participantLimit: 5,
  minTeamSize: 2,
  maxTeamSize: 3,
  deadline: null,
  createdAt: 1,
  requireRealName: true,
  submitterType: 'TEAM',
  teamLockingPolicy: 'LOCK_ON_APPROVAL',
  joined: false,
  creator: { id: 9, username: 'author', nickname: 'Author' },
  category: { id: 1, name: 'Default' },
}

describe('task pages in English', () => {
  it('insights tab', () => {
    render(InsightsView, {
      props: {
        task: TASK as never,
        roster: [
          {
            id: 1,
            member: { id: 1, name: 'Ann', intro: '', avatarId: null },
            createdAt: Date.now() - 10 * 86_400_000,
            updatedAt: Date.now(),
            deadline: null,
            approved: 'APPROVED',
            isTeam: true,
          },
        ] as never,
        reviewByParticipant: new Map(),
        canManage: false,
        loading: false,
        failed: false,
        failureReason: null,
        forbidden: false,
      },
      global: { plugins: plugins() },
    })
    expect(document.body.textContent).toContain(i18n.global.t('tasks.insights.stalled'))
    expect(cjkIn(pageText())).toEqual([])
  })

  it('eligibility alerts for a team task with no eligible team', () => {
    render(TaskEligibilityAlerts, {
      props: {
        task: {
          ...TASK,
          participationEligibility: {
            teams: [
              {
                team: { id: 3, name: 'Blue', handle: 'blue', avatarId: null, memberRealNameStatus: [] },
                eligibility: { eligible: false, reasons: [{ code: 'TEAM_TOO_SMALL', message: '' }] },
              },
            ],
          },
        } as never,
      },
      global: { plugins: plugins(), stubs: { RouterLink: true } },
    })
    expect(cjkIn(pageText())).toEqual([])
  })

  it('team selection and leave dialogs', async () => {
    render(TeamSelectionDialog, {
      props: { open: true, taskData: TASK as never, availableTeams: [], loading: false },
      global: { plugins: plugins() },
    })
    render(LeaveTeamDialog, {
      props: { open: true, taskData: TASK as never, loading: false, joinedTeams: [], selectedTeamId: null },
      global: { plugins: plugins() },
    })
    await waitFor(() => expect(document.body.textContent).toContain('No eligible teams'))
    expect(document.body.textContent).toContain('No teams have joined')
    expect(cjkIn(pageText())).toEqual([])
  })

  it('participation form and privacy notes', () => {
    render(VerifyInfoForm, { props: { requireRealName: true }, global: { plugins: plugins() } })
    render(PrivacyProtectionInfo, { global: { plugins: plugins() } })
    expect(document.body.textContent).toContain('Contact details')
    expect(cjkIn(pageText())).toEqual([])
  })

  it('publish form for a team challenge that requires real names', () => {
    render(TaskForm as Component, {
      props: {
        initialData: { submitterType: 'TEAM', requireRealName: true },
        isEditing: true,
        classificationTopics: [],
      },
      global: { plugins: plugins() },
    })
    expect(document.body.textContent).toContain('Real name required')
    expect(document.body.textContent).toContain('Locked once the team starts')
    expect(cjkIn(pageText())).toEqual([])
  })

  it('market page', async () => {
    render(MarketView, { global: { plugins: plugins() } })
    expect(document.body.textContent).toContain('Market')
    expect(cjkIn(pageText())).toEqual([])
  })
})
