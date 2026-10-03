/**
 * With the interface set to English, the shell reads as English.
 *
 * `catalog.spec.ts` proves the catalog is complete and `lint:i18n` proves no
 * Chinese is typed into `src/`. Neither renders anything, so neither sees a
 * string that is built at import time and never follows the locale, or a
 * `titleKey` that points at a key with no English. This renders the pieces a
 * visitor meets on every page — route titles, banners, the empty page, the
 * access notice, the admin shortcut sheet, and the label tables the workbench
 * reads from — in English and checks that no Chinese reaches the screen.
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import AdminShortcutSheet from '@/components/admin/AdminShortcutSheet.vue'
import BlankPage from '@/components/common/BlankPage.vue'
import OfflineBanner from '@/components/common/OfflineBanner.vue'
import { setLocale, t } from '@/i18n'
import { BOARD_COLUMNS } from '@/lib/board'
import { SLASH_ITEMS } from '@/lib/docSlashMenu'
import { PRIORITY_META, statusMeta } from '@/lib/feedbackMeta'
import { DOCUMENT_TYPES } from '@/lib/fileKind'
import NotFound from '@/views/404.vue'
import ProjectAccessNotice from '@/views/workspace/ProjectAccessNotice.vue'

// Same definition as catalog.spec.ts and the ratchet: Han characters only.
const CJK = /[㐀-䶿一-鿿豈-﫿]/g
const chinese = (text: string) => text.match(CJK)?.join('') ?? ''

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  setLocale('en')
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
})

afterEach(() => {
  cleanup()
  // Overlays teleport outside the mounted root and outlive `cleanup`.
  document.body.innerHTML = ''
})
afterAll(() => setLocale('zh-CN'))

function renderedHtml(component: Component, props: Record<string, unknown> = {}) {
  setActivePinia(createPinia())
  render(component, { props, global: { plugins: [vuetify] } })
  // The whole document, not just the component's root: dialogs and sheets
  // teleport to <body>, and attributes (title, aria-label, placeholder) count.
  // Template comments survive into the test build's DOM and are not copy.
  return document.body.innerHTML.replace(/<!--[\s\S]*?-->/g, '')
}

describe('in English', () => {
  it('every route title resolves to English', async () => {
    const { default: router } = await import('@/router')
    const titled = router.getRoutes().filter((route) => route.meta.titleKey || route.meta.title)
    expect(titled.length).toBeGreaterThan(20)
    const wrong = titled
      .map((route) => {
        const key = route.meta.titleKey as string | undefined
        const title = key ? t(key) : String(route.meta.title)
        return { path: route.path, key, title }
      })
      .filter(({ key, title }) => title === key || chinese(title))
    expect(wrong).toEqual([])
  })

  it.each([
    ['404', NotFound, {}],
    ['blank page', BlankPage, {}],
    ['offline banner', OfflineBanner, {}],
    ['admin shortcut sheet', AdminShortcutSheet, { modelValue: true }],
    ['access notice: signed out', ProjectAccessNotice, { reason: 'unauthenticated' }],
    ['access notice: forbidden', ProjectAccessNotice, { reason: 'forbidden' }],
    ['access notice: archived', ProjectAccessNotice, { reason: 'archived' }],
  ] as const)('%s renders no Chinese', (_, component, props) => {
    const html = renderedHtml(component as Component, props)
    expect(html.length).toBeGreaterThan(0)
    expect(chinese(html)).toBe('')
  })

  it('label tables built at import time follow the locale', () => {
    const labels = [
      ...BOARD_COLUMNS.map((column) => column.label),
      ...SLASH_ITEMS.flatMap((item) => [item.label, item.hint]),
      ...Object.values(PRIORITY_META).map((meta) => meta.label),
      ...(['received', 'in_progress', 'resolved', 'deployed', 'declined'] as const).map((s) => statusMeta(s).label),
      ...Object.values(DOCUMENT_TYPES).map((kind) => kind.label),
    ]
    expect(labels.every(Boolean)).toBe(true)
    expect(labels.filter((label) => chinese(label))).toEqual([])
  })
})
