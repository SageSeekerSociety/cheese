// 应用开着时一直在跑的那几件（plugins/runtime.ts）：装在一个真的 app 上，看它们照样
// 起作用。以前它们写在 App.vue 里，挪出来以后不再有组件替它们兜着。
import { createApp, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { installAppRuntime } from './runtime'

import { appShortcutSheetOpen } from '@/components/common/shortcutSheet'
import i18n from '@/i18n'
import pinia from '@/stores'

vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn(), warning: vi.fn() } }))

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/inbox', component: { render: () => h('div') }, meta: { title: 'Alpha page' } },
    { path: '/', component: { render: () => h('div') }, meta: { publicLanding: true } },
  ],
})

const app = createApp({ render: () => h('div') })
app.use(i18n).use(router).use(pinia)
installAppRuntime(app, router)
app.mount(document.createElement('div'))

function pressQuestionMark(target: EventTarget = document.body) {
  const event = new KeyboardEvent('keydown', { key: '?', bubbles: true, cancelable: true })
  target.dispatchEvent(event)
}

beforeEach(() => {
  appShortcutSheetOpen.value = false
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('while the app is open', () => {
  it('the tab is titled after the page on screen', async () => {
    await router.push('/inbox')
    await router.isReady()
    await nextTick()
    await nextTick()
    expect(document.title).toContain('Alpha page')
  })

  it('? opens the shortcut sheet on an app page', async () => {
    await router.push('/inbox')
    pressQuestionMark()
    expect(appShortcutSheetOpen.value).toBe(true)
  })

  it('? does nothing while typing, or on a public page with no app shell', async () => {
    await router.push('/inbox')
    const field = document.createElement('input')
    document.body.appendChild(field)
    pressQuestionMark(field)
    expect(appShortcutSheetOpen.value).toBe(false)

    await router.push('/')
    pressQuestionMark()
    expect(appShortcutSheetOpen.value).toBe(false)
  })
})
