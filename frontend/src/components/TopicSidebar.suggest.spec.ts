// 智能重命名 in the sidebar row: the field waits for the suggestion, and a name
// the person types while it is still coming is never replaced by it.
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  suggestTopicTitle: vi.fn(),
}))

import TopicSidebar from './TopicSidebar.vue'

import { suggestTopicTitle } from '@/api'
import { setLocale } from '@/i18n'

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, kind = 'topic', title = id): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title,
    kind,
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic
}

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', component: defineComponent({ setup: () => () => h('div') }) }],
})

function mount() {
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  const Host = defineComponent({
    emits: ['rename-topic'],
    setup(_, { emit }) {
      return () =>
        h(VLayout, null, {
          default: () => [
            h(Sidebar, {
              projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
              selectedProjectId: 'p1',
              topics: [topic('root', null, 'root'), topic('room', 'root', 'topic', 'E2E old name')],
              selectedTopicId: 'room',
              loadingTopics: false,
              onRenameTopic: (payload: unknown) => emit('rename-topic', payload),
            }),
          ],
        })
    },
  })
  const vuetify = createVuetify({ components, directives })
  return render(Host, { global: { plugins: [vuetify, router, createPinia()] } })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((yes) => (resolve = yes))
  return { promise, resolve }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
})

beforeAll(() => {
  const g = globalThis as unknown as Record<string, unknown>
  g.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  g.matchMedia ??= () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })
  g.visualViewport ??= {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  }
  g.devicePixelRatio ??= 1
})

async function askForASuggestion(container: Element) {
  const row = Array.from(container.querySelectorAll('.topic-row')).find((el) =>
    el.textContent?.includes('E2E old name')
  )
  await fireEvent.click(row!.querySelector('.row-actions__btn')!)
  await fireEvent.click(await screen.findByText('智能重命名'))
  return (await waitFor(() => {
    const input = container.querySelector('.rename-field input')
    expect(input).not.toBeNull()
    return input
  })) as HTMLInputElement
}

it('the field waits for the suggestion instead of showing the current title', async () => {
  const answer = deferred<{ title: string }>()
  vi.mocked(suggestTopicTitle).mockReturnValueOnce(answer.promise as never)
  const { container } = mount()

  const field = await askForASuggestion(container)
  expect(field.value).toBe('')
  expect(field.placeholder).toBe('正在生成标题…')

  answer.resolve({ title: 'Suggested name' })
  await waitFor(() => expect(field.value).toBe('Suggested name'))
})

it('a name typed while the suggestion is coming is kept and saved', async () => {
  const answer = deferred<{ title: string }>()
  vi.mocked(suggestTopicTitle).mockReturnValueOnce(answer.promise as never)
  const { container, emitted } = mount()

  const field = await askForASuggestion(container)
  await fireEvent.update(field, 'My own name')
  answer.resolve({ title: 'Suggested name' })
  await answer.promise
  await new Promise((r) => setTimeout(r, 0))
  expect(field.value).toBe('My own name')

  await fireEvent.keyUp(field, { key: 'Enter', code: 'Enter' })
  expect(emitted()['rename-topic']).toEqual([[{ id: 'room', title: 'My own name', suggested: false }]])
})
