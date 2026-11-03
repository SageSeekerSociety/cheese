// 页面过期的那条「请刷新」提示是这一页之后唯一会说的话（lib/toast.ts 从那一刻起收住
// 其余失败提示），所以它不能被人划掉：划掉之后什么提示都不剩，askToReload 也不会
// 再弹第二次。这里用真的提示组件（vuetify-sonner），照人划走一条提示的手势去划它。
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { VSonner } from 'vuetify-sonner'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('virtual:pwa-register', () => ({ registerSW: () => async () => {} }))

afterEach(cleanup)

async function paintFirstScreen() {
  const { armAfterFirstPaint, firstPaintDone } = await import('./afterFirstPaint')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: {} }] })
  armAfterFirstPaint(router)
  await router.push('/')
  await vi.waitFor(() => expect(firstPaintDone.value).toBe(true))
}

describe('the request to refresh an outdated page', () => {
  it('stays on screen when the person tries to swipe it away', async () => {
    const { t } = await import('@/i18n')
    const { askToReload } = await import('./apiGeneration')
    render(defineComponent({ render: () => h(VSonner, { position: 'top-center' }) }), {
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await paintFirstScreen()

    askToReload()
    const text = t('global.outdatedClient.message')
    const notice = (await screen.findByText(text)).closest('[data-sonner-toast]') as HTMLElement
    // Pointer capture is the browser's; happy-dom has none to give.
    notice.setPointerCapture = () => {}

    // An upward swipe well past sonner's 20px threshold, as on a top-center toaster.
    await fireEvent.pointerDown(notice, { clientX: 100, clientY: 100, pointerId: 1 })
    await fireEvent.pointerMove(notice, { clientX: 100, clientY: 20, pointerId: 1 })
    await fireEvent.pointerUp(notice, { clientX: 100, clientY: 20, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 600))

    expect(screen.queryByText(text)).not.toBeNull()
  })
})
