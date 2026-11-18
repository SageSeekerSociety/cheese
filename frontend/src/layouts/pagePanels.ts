/**
 * How a page hands its panels to the app shell's layout area (desktop only).
 *
 * On a wide screen the shell lays every page out on one layout area: the sidebar, then
 * the page itself. A page that is more than one panel — a conversation beside a work
 * panel — says so here: which panels it has and how they sit by default. The shell puts
 * them in cells below the page's own head and gives each an outlet; the page renders the
 * panel's content into that outlet (a Teleport), so the content stays the page's own
 * component, with the page's provides and state. A page that says nothing is one panel,
 * and needs nothing from this module.
 *
 * The page never learns which cell a panel is in. It can ask for one panel to fill the
 * area (maximize) and give the space back, and it can hide a panel for now.
 *
 * Where no shell provides this (phone and tablet layouts, a component mounted on its
 * own), `active` is false and the page lays its panels out in place.
 */
import type { ComputedRef, InjectionKey, Ref, ShallowRef } from 'vue'
import type { LayoutNode } from '@/components/layout-area'

import {
  computed,
  getCurrentInstance,
  inject,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  provide,
  reactive,
  ref,
  shallowRef,
  watch,
} from 'vue'

export interface PagePanel {
  id: string
  /** Its cell never gets narrower than this, in px. */
  minWidth?: number
  /** Out of sight for now; it keeps its place and size for when it is shown again. */
  hidden?: boolean
  /** Keep its DOM laid out while it is out of sight (an iframe would reload otherwise). */
  alwaysRender?: boolean
}

export interface PagePanels {
  /** Pages under the same key share the arrangement the reader dragged into shape. */
  key: string
  panels: PagePanel[]
  /** How the page's panels sit by default, below the page's own head. */
  arrangement: LayoutNode
}

interface Claim {
  owner: symbol
  spec: PagePanels
}

export interface PagePanelsHost {
  /** The page whose panels the shell lays out now; null for a page that is one panel. */
  claim: ShallowRef<Claim | null>
  /** Where each of those panels renders, once its cell exists. */
  outlets: Record<string, HTMLElement | undefined>
  /** The panel whose cell fills the area, or null. Not remembered. */
  maximized: Ref<string | null>
}

const HOST: InjectionKey<PagePanelsHost> = Symbol('pagePanels')

/** Called by the shell that lays pages out. */
export function providePagePanels(): PagePanelsHost {
  const host: PagePanelsHost = {
    claim: shallowRef(null),
    outlets: reactive({}),
    maximized: ref(null),
  }
  provide(HOST, host)
  return host
}

export interface PagePanelsHandle {
  /** The shell lays these panels out: render each into `outlet(id)`. */
  active: ComputedRef<boolean>
  outlet: (id: string) => HTMLElement | undefined
  /** The page panel that fills the area now, if any. */
  maximized: ComputedRef<string | null>
  maximize: (id: string) => void
  restore: () => void
}

/**
 * Hands the page's panels to the shell. `spec` is read reactively; returning null (a
 * layout where the page does its own) gives the area back to the page as one panel.
 */
export function usePagePanels(spec: () => PagePanels | null): PagePanelsHandle {
  const host = inject(HOST, null)
  const owner = Symbol('page')
  let shown = true

  function release() {
    if (host?.claim.value?.owner === owner) host.claim.value = null
  }
  function claim() {
    if (!host || !shown) return
    const wanted = spec()
    if (wanted) host.claim.value = { owner, spec: wanted }
    else release()
  }

  if (host) {
    // Synchronously, so the shell sees the new page in the same update that mounts it.
    watch(spec, claim, { immediate: true, flush: 'sync' })
    if (getCurrentInstance()) {
      onBeforeUnmount(release)
      onDeactivated(() => {
        shown = false
        release()
      })
      onActivated(() => {
        shown = true
        claim()
      })
    }
  }

  const active = computed(() => host?.claim.value?.owner === owner)
  const declared = computed(() => (active.value ? host!.claim.value!.spec.panels.map((panel) => panel.id) : []))
  return {
    active,
    outlet: (id) => (active.value ? host!.outlets[id] : undefined),
    maximized: computed(() => {
      const id = host?.maximized.value ?? null
      return id && declared.value.includes(id) ? id : null
    }),
    maximize: (id) => {
      if (host && active.value) host.maximized.value = id
    },
    restore: () => {
      if (host && active.value) host.maximized.value = null
    },
  }
}
