/**
 * In English, the project settings dialogs and the Methods page show no Chinese.
 *
 * Every fixture below is English on purpose: whatever CJK shows up is copy the
 * interface itself put there, not data the test fed in.
 */
import type { Component } from 'vue'
import type { AgentType } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getProjectDefaultModel: vi.fn().mockResolvedValue({ choices: [{ id: 'm1', label: 'Model one' }] }),
    archiveProject: vi.fn(),
    setProjectOwner: vi.fn(),
    listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
    lookupUser: vi.fn().mockResolvedValue({ handle: 'bob', name: 'Bob', avatar_id: null }),
    listTopics: vi.fn().mockResolvedValue({ data: [{ id: 'room-1', title: 'Report room', status: 'active' }] }),
    getProject: vi.fn().mockResolvedValue({ id: 'p1', can_manage_members: true }),
  }
})
vi.mock('@/api/projectSkills', () => {
  const skill = {
    project_id: 'p1',
    title: 'Weekly report',
    description: 'Summarise the week',
    body: '## Steps\n\nCite every number',
    files: { 'scripts/check.py': { sha256: 'a', size: 8 }, 'README.txt': { sha256: 'b', size: 5 } },
    origin: 'cheese',
    proposed_by: 'cheese-x',
    confirmed_by: 'u1',
    confirmed_at: '2026-09-25T00:00:00Z',
    source_topic_id: 'room-1',
    proposal: null,
    created_at: '2026-09-25T00:00:00Z',
    updated_at: '2026-09-25T00:00:00Z',
  }
  return {
    listProjectSkills: vi.fn().mockResolvedValue({
      data: [
        {
          ...skill,
          id: 'draft-1',
          name: 'summary',
          state: 'draft',
          shipped_revision: 2,
          proposal: { reason: 'Wrong order', taught: ['Bad news first'] },
        },
        { ...skill, id: 'live-1', name: 'weekly', state: 'active', shipped_revision: 1 },
      ],
    }),
    getProjectSkill: vi.fn().mockResolvedValue({
      ...skill,
      id: 'live-1',
      name: 'weekly',
      state: 'active',
      shipped_revision: 1,
      contents: { 'scripts/check.py': 'print(1)', 'README.txt': 'notes' },
      revisions: [
        { revision: 1, note: 'first', confirmed_by: 'u1', created_at: '2026-09-25T00:00:00Z', content: skill },
        { revision: 2, note: 'second', confirmed_by: 'u1', created_at: '2026-09-26T00:00:00Z', content: skill },
      ],
    }),
  }
})
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ refreshProjects: vi.fn(), refreshMembers: vi.fn(), projects: [] }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

import AgentEditorDialog from '@/components/agents/AgentEditorDialog.vue'
import ArchiveProjectDialog from '@/components/ArchiveProjectDialog.vue'
import ComputeChoiceForm from '@/components/ComputeChoiceForm.vue'
import TransferProjectDialog from '@/components/TransferProjectDialog.vue'
import i18n, { setLocale } from '@/i18n'
import { displayNameError, handleError, typeLabel } from '@/lib/projectAgents'
import ProjectSkillsView from '@/views/ProjectSkillsView.vue'

const CJK = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/

/** Everything a reader can meet on the page: text plus the attributes that are read out or shown. */
function visibleCopy(): string {
  const attrs = Array.from(document.body.querySelectorAll('*')).flatMap((el) =>
    ['placeholder', 'aria-label', 'title', 'label'].map((name) => el.getAttribute(name) ?? '')
  )
  return [document.body.textContent ?? '', ...attrs].join('\n')
}

function expectNoChinese() {
  const copy = visibleCopy()
  expect(copy.match(new RegExp(`.{0,20}${CJK.source}.{0,20}`, 'g')) ?? []).toEqual([])
}

const mountOpts = () => ({ global: { plugins: [createVuetify({ components, directives }), i18n] } })

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => setLocale('en'))
afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

describe('English settings copy', () => {
  it('the teammate editor, new and existing', async () => {
    const types: AgentType[] = [
      { name: 'reviewer', title: 'Reviewer', description: '', body: '', skills: [], mcp_servers: [], builtin: false },
    ]
    const { rerender } = render(AgentEditorDialog, {
      props: { modelValue: true, projectId: 'p1', agent: null, types },
      ...mountOpts(),
    })
    await screen.findByRole('dialog')
    expectNoChinese()
    await rerender({
      agent: {
        id: 'a1',
        handle: 'rev',
        display_name: 'Rev',
        configuration: { body: '', skills: [], model: null },
      } as never,
    })
    expectNoChinese()
  })

  it('the teammate validation messages', () => {
    for (const message of [
      typeLabel([], null),
      handleError('Bad Handle'),
      displayNameError(''),
      displayNameError('x'.repeat(65)),
    ]) {
      expect(message).toBeTruthy()
      expect(message).not.toMatch(CJK)
    }
  })

  it('the archive dialog', async () => {
    render(ArchiveProjectDialog as unknown as Component, {
      props: { modelValue: true, projectId: 'p1', projectName: 'Thesis' },
      ...mountOpts(),
    })
    await screen.findByRole('dialog')
    expectNoChinese()
  })

  it('the transfer dialog, through to handing the project outside the team', async () => {
    render(TransferProjectDialog as unknown as Component, {
      props: { modelValue: true, projectId: 'p1' },
      ...mountOpts(),
    })
    await screen.findByText('No members can take it over')
    await fireEvent.update(screen.getByLabelText('Full username or email'), 'bob')
    await fireEvent.click(await screen.findByText('Bob', {}, { timeout: 2000 }))
    await screen.findByText(/The whole project moves to/)
    await fireEvent.click(screen.getByRole('button', { name: 'Transfer' }))
    await screen.findByRole('button', { name: 'Confirm transfer' })
    expectNoChinese()
  })

  it('the work computer form, with a custom cloud size', async () => {
    render(ComputeChoiceForm, {
      props: {
        cloudAvailable: true,
        devices: [{ device_id: 'd1', name: 'Lab box', online: true }] as never,
        // The real "not asked yet" state: no range has been read, so the form
        // shows no range numbers at all.
        supply: null,
      },
      ...mountOpts(),
    })
    await fireEvent.input(screen.getByLabelText('Custom CPU, memory and disk'), { target: { checked: true } })
    await screen.findByLabelText('CPU cores')
    expectNoChinese()
  })

  it('the skills page, a skill and its history', async () => {
    const Page = { components: { ProjectSkillsView }, template: '<v-app><ProjectSkillsView project-id="p1" /></v-app>' }
    render(Page as unknown as Component, mountOpts())
    await screen.findByText('Waiting for you')
    expectNoChinese()
    await fireEvent.click(screen.getAllByRole('button', { name: 'Weekly report' })[0]!)
    await screen.findByText('Invoked as summary')
    expectNoChinese()
    await fireEvent.click(screen.getByRole('tab', { name: 'Version history' }))
    await fireEvent.click((await screen.findAllByRole('button', { name: 'View' }))[0]!)
    expectNoChinese()
  })
})
