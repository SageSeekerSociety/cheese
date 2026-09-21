import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ create: vi.fn(), push: vi.fn() }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: {} }), useRouter: () => ({ push: state.push }) }))
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp: true }) }))
vi.mock('@/lib/routePrefetch', () => ({ cancelPrefetch: vi.fn(), prefetchOnHover: vi.fn() }))
vi.mock('./TopicView.vue', () => ({ default: {} }))
import ProjectSidebar from './ProjectSidebar.vue'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
it('shows creation progress and submits once during repeated clicks', async () => {
  let resolve!: (value: unknown) => void
  state.create.mockReturnValueOnce(
    new Promise((yes) => {
      resolve = yes
    })
  )
  const view = render(ProjectSidebar, {
    props: { projectId: 'p' },
    global: {
      stubs: {
        TopicSidebar: {
          props: ['creatingTopic'],
          template:
            '<button @click="$emit(\'create-topic\', \'\')">{{ creatingTopic ? "creating" : "create" }}</button>',
        },
      },
    },
  })
  await fireEvent.click(view.getByRole('button'))
  await fireEvent.click(view.getByRole('button'))
  expect(view.getByRole('button').textContent).toBe('creating')
  expect(state.create).toHaveBeenCalledTimes(1)
  resolve({ id: 'new', project_id: 'p' })
  await vi.waitFor(() =>
    expect(state.push).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p', topicId: 'new' } })
  )
})
