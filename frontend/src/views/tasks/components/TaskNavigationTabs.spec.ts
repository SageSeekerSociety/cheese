import type { Task } from '@/types'

import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import TaskNavigationTabs from './TaskNavigationTabs.vue'

afterEach(cleanup)

it('keeps task links resolvable while registration navigates out of the space', async () => {
  const names = ['TasksDetail', 'TasksParticipants', 'TasksSubmissions', 'TasksSubmit', 'TasksAIAdvice']
  const suffixes = ['', '/participants', '/submissions', '/submit', '/ai-advice']
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      ...names.map((name, index) => ({
        path: `/spaces/:spaceId/tasks/:taskId${suffixes[index]}`,
        name,
        component: { template: '<div />' },
      })),
      { path: '/projects/:projectId', component: { template: '<div />' } },
    ],
  })
  await router.push('/spaces/7/tasks/42')
  const { container } = render(TaskNavigationTabs, {
    props: {
      taskData: { id: 42, space: { id: 7 }, joined: true, submittable: true } as Task,
      isCreator: true,
      isAdmin: true,
    },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })

  // Shared navigation remains mounted until the outgoing task view unmounts.
  await router.push('/projects/shared-project')
  await nextTick()
  expect(Array.from(container.querySelectorAll('a'), (link) => link.getAttribute('href'))).toEqual(
    suffixes.map((suffix) => `/spaces/7/tasks/42${suffix}`)
  )
})
