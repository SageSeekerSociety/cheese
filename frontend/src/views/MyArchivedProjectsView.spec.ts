/** 已归档的项目：只列我归档的那些，取消归档之后它从这里离开、回到项目清单。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const listArchivedProjects = vi.fn()
const unarchiveProject = vi.fn()
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listArchivedProjects: (...a: unknown[]) => listArchivedProjects(...a),
  unarchiveProject: (...a: unknown[]) => unarchiveProject(...a),
}))

const refreshProjects = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ refreshProjects }) }))

import MyArchivedProjectsView from './MyArchivedProjectsView.vue'

const archived = (id: string, name: string) => ({
  id,
  name,
  created_at: '2026-09-01T00:00:00Z',
  archived_at: '2026-09-28T00:00:00Z',
})

function mount() {
  return render(MyArchivedProjectsView, {
    global: {
      plugins: [createVuetify({ components, directives })],
    },
  })
}

afterEach(cleanup)

beforeEach(() => {
  listArchivedProjects.mockReset().mockResolvedValue({ data: [archived('a', '毕业设计'), archived('b', '周报')] })
  unarchiveProject.mockReset().mockResolvedValue({})
  refreshProjects.mockReset().mockResolvedValue(undefined)
})

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

describe('已归档的项目', () => {
  it('列出我归档的项目', async () => {
    mount()
    expect(await screen.findByText('毕业设计')).toBeTruthy()
    expect(screen.getByText('周报')).toBeTruthy()
  })

  it('没有的时候说暂无', async () => {
    listArchivedProjects.mockResolvedValue({ data: [] })
    mount()
    expect(await screen.findByText('暂无已归档的项目')).toBeTruthy()
  })

  it('reads in English under the en locale', async () => {
    setLocale('en')
    listArchivedProjects.mockResolvedValue({ data: [] })
    mount()
    expect(await screen.findByText('No archived projects')).toBeTruthy()
  })

  it('取消归档之后它离开这里，项目清单跟着刷新', async () => {
    mount()
    await screen.findByText('毕业设计')
    const [first] = screen.getAllByRole('button', { name: '取消归档' })
    await fireEvent.click(first)
    await waitFor(() => expect(unarchiveProject).toHaveBeenCalledWith('a'))
    await waitFor(() => expect(screen.queryByText('毕业设计')).toBeNull())
    expect(screen.getByText('周报')).toBeTruthy()
    expect(refreshProjects).toHaveBeenCalled()
  })
})
