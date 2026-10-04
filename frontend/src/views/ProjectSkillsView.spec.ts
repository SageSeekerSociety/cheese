/**
 * 技能：芝士整理或改过的要人点保存；芝士的改动可以整个放弃，回到正在用的那一版；
 * 旧版本能恢复；新建不用选房间；导入只给项目管理员，读出来先看，添加才算数。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectSkillsView from './ProjectSkillsView.vue'

import { headerCommands } from '@/commands'
import i18n, { setLocale } from '@/i18n'

const routeQuery: Record<string, string> = {}
vi.mock('vue-router', () => ({ useRoute: () => ({ query: routeQuery }), useRouter: () => ({ push: vi.fn() }) }))

// 确认框回什么由这一格决定：`wait` 解出真就是人点了确定。
const dialog = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: dialog.confirm }),
}))

vi.mock('../api', () => ({ getProject: vi.fn() }))
vi.mock('../api/projectSkills', () => ({
  listProjectSkills: vi.fn(),
  getProjectSkill: vi.fn(),
  addProjectSkill: vi.fn(),
  previewSkillImport: vi.fn(),
  updateProjectSkill: vi.fn(),
  confirmProjectSkill: vi.fn(),
  declineProjectSkill: vi.fn(),
  restoreProjectSkill: vi.fn(),
  deleteProjectSkill: vi.fn(),
}))

const { getProject } = await import('../api')
const {
  addProjectSkill,
  confirmProjectSkill,
  declineProjectSkill,
  deleteProjectSkill,
  getProjectSkill,
  listProjectSkills,
  previewSkillImport,
  restoreProjectSkill,
} = await import('../api/projectSkills')

afterEach(cleanup)

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
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

const content = {
  title: '项目周报',
  description: '把一周进展整理成一页周报',
  body: '## 步骤与规则\n\n数字都标来源',
  files: {},
}
const base = {
  ...content,
  project_id: 'p1',
  name: 'weekly-report',
  origin: 'cheese' as const,
  proposed_by: 'cheese-x',
  confirmed_by: 'u1',
  confirmed_at: '2026-09-25T00:00:00Z',
  source_topic_id: 'room-1',
  proposal: null,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

function manager(yes: boolean) {
  vi.mocked(getProject).mockResolvedValue({ id: 'p1', can_manage_members: yes } as never)
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialog.confirm.mockImplementation(() => ({ wait: async () => true }))
  for (const k of Object.keys(routeQuery)) delete routeQuery[k]
  manager(false)
  vi.mocked(listProjectSkills).mockResolvedValue({
    data: [
      {
        ...base,
        id: 'new-1',
        name: 'summary',
        title: '芝士刚整理的摘要方法',
        state: 'draft',
        shipped_revision: 0,
        proposal: { taught: ['先说结论'] },
      },
      { ...base, id: 'edit-1', title: '被芝士改过的周报', state: 'draft', shipped_revision: 2 },
      { ...base, id: 'live-1', name: 'standup', title: '站会纪要', state: 'active', shipped_revision: 1 },
    ],
    total: 3,
  })
})

// 包一层 `<v-app>`：详情是 `VNavigationDrawer`，它要读 Vuetify 注入的 layout。
function mount() {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { ProjectSkillsView },
    template: '<v-app><ProjectSkillsView project-id="p1" /></v-app>',
  }
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify, i18n] } })
}

function buttonIn(scope: ParentNode, label: string): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

const detail = () => document.body.querySelector('[data-skill-detail]')
const action = (name: string) => document.body.querySelector<HTMLElement>(`[data-action="${name}"]`)

async function openRow(container: Element, id: string, title: string) {
  await waitFor(() => expect(container.textContent).toContain(title))
  await fireEvent.click(buttonIn(container.querySelector(`[data-skill="${id}"]`)!, title)!)
  await waitFor(() => expect(detail()?.getAttribute('data-skill-detail')).toBe(id))
}

function command(id: string) {
  return headerCommands.value.find((c) => c.id === id)
}

describe('技能', () => {
  it('从房间的「查看」点进来，那一份的详情直接打开', async () => {
    routeQuery.skill = 'new-1'
    mount()
    await waitFor(() => expect(detail()?.getAttribute('data-skill-detail')).toBe('new-1'))
  })

  it('芝士整理的要人点保存才保存', async () => {
    vi.mocked(confirmProjectSkill).mockResolvedValue({
      ...base,
      id: 'new-1',
      title: '芝士刚整理的摘要方法',
      state: 'active',
      shipped_revision: 1,
    })
    const { container } = mount()
    await openRow(container, 'new-1', '芝士刚整理的摘要方法')
    expect(confirmProjectSkill).not.toHaveBeenCalled()

    await fireEvent.click(action('save')!)

    expect(confirmProjectSkill).toHaveBeenCalledWith('new-1')
  })

  it('已保存的那一份没有保存这一步', async () => {
    const { container } = mount()
    await openRow(container, 'live-1', '站会纪要')
    expect(action('save')).toBeNull()
  })

  it('不保存芝士提议的新一份，记成拒绝而不是删掉', async () => {
    vi.mocked(declineProjectSkill).mockResolvedValue({ ...base, id: 'new-1', state: 'draft', shipped_revision: 0 })
    const { container } = mount()
    await openRow(container, 'new-1', '芝士刚整理的摘要方法')

    await fireEvent.click(action('decline')!)

    expect(declineProjectSkill).toHaveBeenCalledWith('new-1')
    expect(deleteProjectSkill).not.toHaveBeenCalled()
  })

  it('放弃芝士的改动，回到正在用的那一版', async () => {
    vi.mocked(restoreProjectSkill).mockResolvedValue({ ...base, id: 'edit-1', state: 'active', shipped_revision: 2 })
    const { container } = mount()
    await openRow(container, 'edit-1', '被芝士改过的周报')

    await fireEvent.click(action('discard')!)

    await waitFor(() => expect(restoreProjectSkill).toHaveBeenCalledWith('edit-1', 2))
  })

  it('放弃要人点过确认才动：说取消，那一版改动还在', async () => {
    dialog.confirm.mockImplementation(() => ({ wait: async () => false }))
    const { container } = mount()
    await openRow(container, 'edit-1', '被芝士改过的周报')

    await fireEvent.click(action('discard')!)

    expect(dialog.confirm).toHaveBeenCalledTimes(1)
    expect(restoreProjectSkill).not.toHaveBeenCalled()
  })

  it('历史版本里能把旧的一版恢复回来', async () => {
    vi.mocked(getProjectSkill).mockResolvedValue({
      ...base,
      id: 'live-1',
      name: 'standup',
      title: '站会纪要',
      state: 'active',
      shipped_revision: 2,
      revisions: [
        {
          revision: 2,
          content: { ...content, body: '新规则' },
          confirmed_by: 'u1',
          note: '修改',
          created_at: '2026-09-25T01:00:00Z',
        },
        { revision: 1, content, confirmed_by: 'u1', note: '创建', created_at: '2026-09-25T00:00:00Z' },
      ],
    })
    vi.mocked(restoreProjectSkill).mockResolvedValue({ ...base, id: 'live-1', state: 'active', shipped_revision: 3 })
    const { container } = mount()
    await openRow(container, 'live-1', '站会纪要')

    await fireEvent.click(buttonIn(detail()!, '历史版本')!)
    await waitFor(() => expect(document.body.querySelector('[data-revision="1"]')).toBeTruthy())
    await fireEvent.click(buttonIn(document.body.querySelector('[data-revision="1"]')!, '恢复到这一版')!)

    expect(restoreProjectSkill).toHaveBeenCalledWith('live-1', 1)
  })

  it('不是项目管理员，页头上没有导入', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('站会纪要'))
    expect(command('skills.import')).toBeUndefined()
    expect(command('skills.new')).toBeTruthy()
  })

  it('导入：读出来只是预览，点添加才加进项目，记成导入的', async () => {
    manager(true)
    vi.mocked(previewSkillImport).mockResolvedValue({
      name: 'pdf',
      title: 'PDF 处理',
      description: '需要处理 PDF 时',
      body: '先取文本',
      files: { 'scripts/merge.py': 'print(1)\n' },
      skipped: [],
      scripts: 1,
    })
    vi.mocked(addProjectSkill).mockResolvedValue({
      ...base,
      id: 'pdf-1',
      name: 'pdf',
      title: 'PDF 处理',
      origin: 'import',
      state: 'active',
      shipped_revision: 1,
    })
    const { container } = mount()
    await waitFor(() => expect(command('skills.import')).toBeTruthy())
    command('skills.import')!.run!()

    await waitFor(() => expect(document.body.querySelector('[data-import-url]')).toBeFalsy())
    await fireEvent.click(buttonIn(document.body, 'GitHub 地址')!)
    const url = await waitFor(() => document.body.querySelector<HTMLInputElement>('[data-import-url] input')!)
    await fireEvent.update(url, 'https://github.com/anthropics/skills/tree/main/skills/pdf')
    await fireEvent.click(buttonIn(document.body, '读取')!)

    await waitFor(() => expect(document.body.querySelector('[data-import-scripts]')).toBeTruthy())
    expect(previewSkillImport).toHaveBeenCalledWith('p1', {
      url: 'https://github.com/anthropics/skills/tree/main/skills/pdf',
    })
    expect(addProjectSkill).not.toHaveBeenCalled()

    await fireEvent.click(buttonIn(document.body, '添加')!)

    await waitFor(() =>
      expect(addProjectSkill).toHaveBeenCalledWith('p1', {
        name: 'pdf',
        title: 'PDF 处理',
        description: '需要处理 PDF 时',
        body: '先取文本',
        files: { 'scripts/merge.py': 'print(1)\n' },
        imported: true,
      })
    )
    await waitFor(() => expect(container.textContent).toContain('PDF 处理'))
  })

  it('新建不用选房间，存进这个项目', async () => {
    vi.mocked(addProjectSkill).mockResolvedValue({
      ...base,
      id: 'mine-1',
      name: 'grading',
      title: '批改作业',
      origin: 'person',
      state: 'active',
      shipped_revision: 1,
    })
    mount()
    await waitFor(() => expect(command('skills.new')).toBeTruthy())
    command('skills.new')!.run!()

    const fields = await waitFor(() => {
      const inputs = document.body.querySelectorAll<HTMLInputElement>('.v-dialog input, .v-dialog textarea')
      expect(inputs.length).toBeGreaterThan(3)
      return inputs
    })
    await fireEvent.update(fields[0], '批改作业')
    await fireEvent.update(fields[2], '老师让批改一批作业时')
    await fireEvent.click(buttonIn(document.body, '保存')!)

    await waitFor(() => expect(addProjectSkill).toHaveBeenCalledTimes(1))
    const [projectId, sent] = vi.mocked(addProjectSkill).mock.calls[0]
    expect(projectId).toBe('p1')
    expect(sent.title).toBe('批改作业')
    expect(sent.name).toMatch(/^[a-z0-9][a-z0-9-]{1,47}$/)
    expect(sent.imported).toBeUndefined()
  })
})
