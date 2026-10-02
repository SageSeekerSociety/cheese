// The hover flyout on a rail tile (name + ⌘N) must not outlive the pointer.
//
// On the live site a flyout like 「测试 ⌘ 4」 sometimes stayed on screen with the
// pointer long gone. Vuetify closes a tooltip only on the tile's own mouseleave or
// blur (it forces `persistent`, so no outside click or Escape), and neither is
// guaranteed: a flyout opened by keyboard focus ignores the pointer, a rail hidden
// by keep-alive leaves its flyout in <body>, and a browser can drop a mouseleave.
// So these specs move the pointer elsewhere WITHOUT a mouseleave and ask whether
// the flyout is still showing.
import type { NavGenericItem } from './types'

import { nextTick } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import RailItem from './RailItem.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
})

const tile: NavGenericItem = {
  key: 'cx-t',
  type: 'item',
  title: '测试',
  to: '/project/t',
  img: 'data:image/svg+xml,<svg/>',
  projectId: 't',
  shortcut: 4,
}

beforeAll(() => {
  // Vuetify's overlay touches these browser APIs (and measures the viewport and
  // pixel ratio once it opens); happy-dom lacks them.
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

/** The flyout is rendered at <body> (eagerly, hidden); "showing" means not display:none. */
function flyoutShowing(): boolean {
  const el = document.querySelector<HTMLElement>('.rail-flyout')
  return !!el && el.style.display !== 'none'
}

function pointerMoveOn(target: Element) {
  target.dispatchEvent(new Event('pointermove', { bubbles: true }))
}

async function hoverTile() {
  const view = render(RailItem, {
    props: { item: tile },
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  const link = view.container.querySelector('a[href="/project/t"]') as HTMLElement
  // Vuetify binds the hover listeners to the tile a tick after mount.
  await nextTick()
  // A real `mouseenter`: testing-library's fireEvent.mouseEnter dispatches mouseover.
  link.dispatchEvent(new MouseEvent('mouseenter'))
  await waitFor(() => expect(flyoutShowing()).toBe(true))
  expect(document.querySelector('.rail-flyout')?.textContent).toContain('测试')
  return { view, link }
}

describe('rail tile flyout', () => {
  it('goes away once the pointer moves elsewhere, even if the tile never heard it leave', async () => {
    const { view } = await hoverTile()

    pointerMoveOn(document.body)

    await waitFor(() => expect(flyoutShowing()).toBe(false))
    view.unmount()
  })

  it('stays while the pointer is still moving over the tile', async () => {
    const { view, link } = await hoverTile()

    pointerMoveOn(link.querySelector('.v-img') ?? link)
    await new Promise((r) => setTimeout(r, 50))

    expect(flyoutShowing()).toBe(true)
    view.unmount()
  })
})
