/**
 * How a page hands its panels to the app shell's layout area (desktop only).
 *
 * On a wide screen the shell lays every page out on one layout area: the sidebar, then
 * the page itself. A page that is more than one panel — a conversation beside a work
 * panel — says so here: which panels it has and how they sit by default. The shell puts
 * them in cells below the page's own head and gives each an outlet; the page renders the
 * panel's content into that outlet (a Teleport), so the content stays the page's own
 * component, with the page's provides and state. A titled panel (`titled`) gets a second
 * outlet in its title bar (`headOutlet`), for what the bar says before its buttons. A page that says nothing is one panel,
 * and needs nothing from this module.
 *
 * The page never learns which cell a panel is in. It can ask for a panel to be shown (its
 * cell opens and it becomes the visible tab there), for one panel's cell to fill the area
 * (maximize) and give the space back, and it can hide a panel for now. It hears which tab
 * the reader picked, which panels are on screen, and a transient tab closed or kept.
 *
 * Where no shell provides this (phone and tablet layouts, a component mounted on its
 * own), `active` is false and the page lays its panels out in place.
 */
import type { ComputedRef, InjectionKey, Ref, ShallowRef } from 'vue'
import type { LayoutNode, LayoutPanel } from '@/components/layout-area'

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

/**
 * A page's panel, as the layer takes it (titles, signals, closing, groups, a width of its
 * own…), less what the shell owns.
 */
export type PagePanel = Omit<LayoutPanel, 'component' | 'props' | 'fitHeight' | 'foldKey' | 'pinned'>

export interface PagePanels {
  /** Pages under the same key share the arrangement the reader dragged into shape. */
  key: string
  panels: PagePanel[]
  /** How the page's panels sit by default, below the page's own head. */
  arrangement: LayoutNode
  /** The reader picked this panel's tab. */
  onActivated?: (id: string) => void
  /** The reader closed the tab of a transient panel: drop it. */
  onClose?: (id: string) => void
  /** The reader double-clicked the tab of a temporary panel: keep it. */
  onKeep?: (id: string) => void
  /** The reader picked, under "+", a kind of panel the page opens anew each time: open one. */
  onSpawn?: (id: string) => void
  /** Height of the tab strips, when they sit beside a header of the page's own. */
  tabStripHeight?: string
}

/** What the shell's layout area does for a page. */
export interface PagePanelsArea {
  open: (id: string) => void
  /** The reader has moved something away from the page's default arrangement. */
  readonly rearranged: boolean
  reset: () => void
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
  /** Where a titled panel's title bar takes what the page puts in it, once the bar exists. */
  headOutlets: Record<string, HTMLElement | undefined>
  /** The panel whose cell fills the area, or null. Not remembered. */
  maximized: Ref<string | null>
  /** The panels on screen now. */
  visible: Ref<string[]>
  /** The shell's layout area, while it is mounted. */
  area: ShallowRef<PagePanelsArea | null>
  /** A panel a page asked to show before the area was mounted. */
  pendingShow: Ref<string | null>
}

const HOST: InjectionKey<PagePanelsHost> = Symbol('pagePanels')

/** Called by the shell that lays pages out. */
export function providePagePanels(): PagePanelsHost {
  const host: PagePanelsHost = {
    claim: shallowRef(null),
    outlets: reactive({}),
    headOutlets: reactive({}),
    maximized: ref(null),
    visible: ref([]),
    area: shallowRef(null),
    pendingShow: ref(null),
  }
  // The area mounts after the page has started asking: what it asked for is shown then.
  watch(host.area, (area) => {
    if (!area || !host.pendingShow.value) return
    area.open(host.pendingShow.value)
    host.pendingShow.value = null
  })
  provide(HOST, host)
  return host
}

export interface PagePanelsHandle {
  /** The shell lays these panels out: render each into `outlet(id)`. */
  active: ComputedRef<boolean>
  outlet: (id: string) => HTMLElement | undefined
  /** A titled panel's title bar: render what it says (before its buttons) into `headOutlet(id)`. */
  headOutlet: (id: string) => HTMLElement | undefined
  /** The page panel that fills the area now, if any. */
  maximized: ComputedRef<string | null>
  maximize: (id: string) => void
  restore: () => void
  /** The page's panels on screen now (the visible tab of each cell in sight). */
  visible: ComputedRef<ReadonlySet<string>>
  /** Shows a panel: brings it back if it was closed, opens its cell, makes it the visible tab. */
  show: (id: string) => void
  /** The reader has moved the page's panels away from its default arrangement. */
  rearranged: ComputedRef<boolean>
  /** Back to the page's default arrangement. */
  reset: () => void
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
    headOutlet: (id) => (active.value ? host!.headOutlets[id] : undefined),
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
    visible: computed(
      () => new Set(active.value ? host!.visible.value.filter((id) => declared.value.includes(id)) : [])
    ),
    show: (id) => {
      if (!host || !active.value) return
      if (host.area.value) host.area.value.open(id)
      else host.pendingShow.value = id
    },
    rearranged: computed(() => active.value && host!.area.value?.rearranged === true),
    reset: () => {
      if (host && active.value) host.area.value?.reset()
    },
  }
}
