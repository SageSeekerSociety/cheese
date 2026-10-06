// The nickname tooltip on the account avatar must not outlive the pointer.
//
// Vuetify forces `persistent` on v-tooltip, so the only thing that ever closed this
// one was the avatar's own mouseleave — never an outside click, never Escape. The
// rail's tiles had the same defect and were fixed by watching the pointer (#2455);
// the avatar is the same tooltip written as a directive, where there is no v-model
// to close it with, so it was left behind. These specs move the pointer elsewhere
// WITHOUT a mouseleave and ask whether the tooltip is still showing.
import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import LeftAppRail from './LeftAppRail.vue'

// The rail builds its menu out of the signed-in account; these specs only need an
// avatar to hover, so the composable is stubbed (same shape as UserMenuCard.spec.ts).
const menuStub = vi.hoisted(() => ({
  menuOpen: { value: false },
  loggedIn: { value: true },
  currentUser: { value: { id: 42, username: 'alice' } },
  avatar: { value: null },
  avatarInitial: { value: '爱' },
  avatarColor: { value: '#6a5acd' },
  nickname: { value: '爱丽丝' },
  intro: { value: '' },
  onLogout: () => {},
}))

vi.mock('@/composables/useUserMenu', () => ({ useUserMenu: () => menuStub }))

const router = createRouter({
  history: createWebHashHistory(),
  routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
})

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

/** The tooltip is rendered eagerly and hidden; "showing" means not display:none. */
function tooltipShowing(): boolean {
  const el = document.querySelector<HTMLElement>('.v-tooltip .v-overlay__content')
  return !!el && el.style.display !== 'none'
}

function pointerMoveOn(target: Element) {
  target.dispatchEvent(new Event('pointermove', { bubbles: true }))
}

async function hoverAvatar() {
  // The rail is a v-navigation-drawer, which reads Vuetify's layout — VLayout is
  // what provides it, and the drawer throws without one.
  const Rail = {
    components: { LeftAppRail },
    template: '<v-layout><LeftAppRail :items="[]" /></v-layout>',
  }
  const view = render(Rail, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  // 头像是 UserAvatar（它的根是 `.v-avatar`），但菜单和 tooltip 的锚点是包着它的
  // 那层 `.rail-avatar`（UserAvatar 没开插槽，tooltip 塞不进它的根）。事件要打在锚点
  // 那层上——pointerenter / mouseenter 都不冒泡，打在内层 `.v-avatar` 上收不到。
  const avatar = view.container.querySelector('.rail-avatar') as HTMLElement
  // Vuetify binds the hover listeners to the activator a tick after mount.
  await nextTick()
  // A real pointer entering fires pointerenter first, then mouseenter. The component
  // remembers the activator from the former (a template ref would be overwritten by
  // the menu's own ref), and the tooltip opens on the latter.
  avatar.dispatchEvent(new Event('pointerenter'))
  // A real `mouseenter`: testing-library's fireEvent.mouseEnter dispatches mouseover.
  avatar.dispatchEvent(new MouseEvent('mouseenter'))
  await waitFor(() => expect(tooltipShowing()).toBe(true))
  expect(document.querySelector('.v-tooltip .v-overlay__content')?.textContent).toContain('爱丽丝')
  return { view, avatar }
}

describe('account avatar tooltip', () => {
  it('goes away once the pointer moves elsewhere, even if the avatar never heard it leave', async () => {
    const { view } = await hoverAvatar()

    pointerMoveOn(document.body)

    await waitFor(() => expect(tooltipShowing()).toBe(false))
    view.unmount()
  })

  it('stays while the pointer is still moving over the avatar', async () => {
    const { view, avatar } = await hoverAvatar()

    pointerMoveOn(avatar)
    await new Promise((r) => setTimeout(r, 50))

    expect(tooltipShowing()).toBe(true)
    view.unmount()
  })
})
