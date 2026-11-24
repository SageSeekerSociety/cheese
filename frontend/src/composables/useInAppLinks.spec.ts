// 正文里指向站内页面的链接：哪一些该由应用换页，哪一些该交给浏览器。
import { defineComponent, h, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { clickedInAppHref, inAppHrefFor, useInAppLinks } from './useInAppLinks'

const blank = { render: () => h('div') }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: blank },
      { path: '/', name: 'HomeHub', component: blank },
      { path: '/:pathMatch(.*)*', name: 'NotFound', component: blank },
    ],
  })
}

function anchor(href: string, target?: string): HTMLAnchorElement {
  const a = document.createElement('a')
  a.setAttribute('href', href)
  if (target) a.setAttribute('target', target)
  document.body.append(a)
  return a
}

describe('which links the app opens itself', () => {
  const router = makeRouter()

  it('takes a path the router knows, in place', () => {
    expect(inAppHrefFor(anchor('/spaces/3/tasks/9'), router)).toBe('/spaces/3/tasks/9')
  })

  it('leaves a link out, and one that opens in another window, to the browser', () => {
    expect(inAppHrefFor(anchor('https://example.com/x'), router)).toBeNull()
    expect(inAppHrefFor(anchor('mailto:a@b.com'), router)).toBeNull()
    expect(inAppHrefFor(anchor('//example.com/x'), router)).toBeNull()
    expect(inAppHrefFor(anchor('/spaces/3/tasks/9', '_blank'), router)).toBeNull()
  })

  // `/docs/…` is served by the deployment, not a route of this app: it falls to
  // NotFound, and a 404 is not where the reader wanted to go.
  it('leaves a path no route of the app claims to the browser', () => {
    expect(inAppHrefFor(anchor('/docs/projects'), router)).toBeNull()
  })

  it('honours the click: a modified or non-left click opens a new one', () => {
    const link = anchor('/spaces/3/tasks/9')
    for (const key of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey'] as const) {
      const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
      Object.defineProperty(event, key, { value: true })
      Object.defineProperty(event, 'target', { value: link })
      expect(clickedInAppHref(event, router)).toBeNull()
    }
  })
})

const Host = defineComponent({
  setup() {
    const el = ref<HTMLElement | null>(null)
    useInAppLinks(el)
    return () => h('div', { ref: el })
  },
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('following a link in a reading surface', () => {
  it('turns a page here instead of reloading the app', async () => {
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const push = vi.spyOn(router, 'push')
    const { container } = render(Host, { global: { plugins: [router] } })
    await nextTick()
    const a = document.createElement('a')
    a.setAttribute('href', '/spaces/3/tasks/9')
    a.textContent = '查看完整赛题要求'
    container.querySelector('div')!.append(a)
    const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
    a.dispatchEvent(event)
    await waitFor(() => expect(push).toHaveBeenCalledWith('/spaces/3/tasks/9'))
    expect(event.defaultPrevented).toBe(true)
  })
})
