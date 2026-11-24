// 资料库 › 项目文件在桌面上：看不需要任务，打开的几份排成一排页签；按「编辑」的那一刻才开一件任务，
// 进那件任务，翻到的还是这一份。
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vuetify', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vuetify')>()),
  useDisplay: () => ({ mdAndUp: { value: true }, smAndDown: { value: false }, width: { value: 1400 } }),
}))

const api = vi.hoisted(() => ({
  listFiles: vi.fn(),
  listProjectChanges: vi.fn(),
  projectFileBytes: vi.fn(),
  editProjectFile: vi.fn(),
  createRoomTask: vi.fn(),
}))
vi.mock('@/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api')>()), ...api }))

import { setLocale, t } from '@/i18n'
import ProjectFiles from '@/views/library/ProjectFiles.vue'

const PROJECT = '5c2d1e0f-1f22-4a33-9c44-0000000000aa'
const ROOM = '5c2d1e0f-1f22-4a33-9c44-0000000000bb'
const TASK = '5c2d1e0f-1f22-4a33-9c44-0000000000cc'

afterEach(cleanup)
beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  for (const fn of Object.values(api)) fn.mockReset()
  api.listFiles.mockResolvedValue({
    data: [
      { path: 'README.md', bytes: 10 },
      { path: 'src/stack.c', bytes: 20 },
      { path: 'src/queue.c', bytes: 20 },
    ],
  })
  api.listProjectChanges.mockResolvedValue([])
  api.projectFileBytes.mockResolvedValue(new TextEncoder().encode('int top;\n').buffer)
})

async function mount() {
  const Empty = defineComponent({ setup: () => () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { name: 'project-library', path: '/p/:projectId/library', component: Empty },
      {
        name: 'project-library-files',
        path: '/p/:projectId/library/files',
        component: ProjectFiles,
        props: (route) => ({ projectId: route.params.projectId }),
      },
      { name: 'workspace-task', path: '/p/:projectId/c/:topicId/t/:taskId', component: Empty },
    ],
  })
  await router.push(`/p/${PROJECT}/library/files`)
  render(defineComponent({ setup: () => () => h(RouterView) }), {
    global: {
      plugins: [router, createVuetify({ components, directives })],
      stubs: { FileBytesPreview: true },
    },
  })
  await screen.findByText('src')
  return router
}

const tabs = () => screen.queryByRole('group', { name: t('work.room.taskFiles.openFiles') })?.textContent ?? ''

describe('资料库里的项目文件（桌面）', () => {
  it('看文件不开任务：打开的几份排成一排页签', async () => {
    await mount()
    await fireEvent.click(screen.getByText('src'))
    await fireEvent.click(await screen.findByText('stack.c'))
    await waitFor(() => expect(tabs()).toContain('stack.c'))
    await fireEvent.click(screen.getByText('queue.c'))

    await waitFor(() => expect(tabs()).toContain('queue.c'))
    expect(tabs()).toContain('stack.c')
    expect(api.editProjectFile).not.toHaveBeenCalled()
    expect(api.createRoomTask).not.toHaveBeenCalled()
  })

  it('按「编辑」开一件任务来改翻到的那一份，进那件任务', async () => {
    api.editProjectFile.mockResolvedValue({ id: TASK, room_id: ROOM, project_id: PROJECT, title: 'stack.c' })
    const router = await mount()
    await fireEvent.click(screen.getByText('src'))
    await fireEvent.click(await screen.findByText('stack.c'))
    await waitFor(() => expect(tabs()).toContain('stack.c'))

    await fireEvent.click(screen.getByRole('button', { name: t('work.library.projectFiles.edit') }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-task'))
    expect(api.editProjectFile).toHaveBeenCalledWith(PROJECT, 'src/stack.c')
    expect(router.currentRoute.value.params).toMatchObject({ topicId: ROOM, taskId: TASK })
  })
})
