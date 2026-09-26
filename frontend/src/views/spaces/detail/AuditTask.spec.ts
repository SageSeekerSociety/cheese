import { createVuetify } from 'vuetify'
import { render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  list: vi.fn().mockResolvedValue({ data: { tasks: [], page: { page_start: null, page_size: 20, has_more: false } } }),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: mocks }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/plugins/dialog', () => ({ useDialog: () => ({}), CancelError: class extends Error {} }))
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import { useSpaceStore } from '@/stores/space'

import AuditTask from './AuditTask.vue'

describe('审核题目', () => {
  it('loads the waiting tasks once the space is known, even when opened before it', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSpaceStore()
    const view = render(AuditTask, { global: { plugins: [pinia, createVuetify()], stubs: { TipTapViewer: true } } })
    expect(mocks.list).not.toHaveBeenCalled()

    store.currentSpaceId = 8
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(expect.objectContaining({ space: 8, approved: 'NONE' })))
    view.unmount()
  })
})
