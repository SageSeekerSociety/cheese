// 面板在哪儿找、找了什么回来。
//
// 三件事拴在一起，所以合成一处：范围（默认当前页所在的项目，可以在面板里换进去
// 另一个项目）、远程搜（停止打字一会儿再问，问回来时输入已经变了就扔掉），以及最后
// 交出来的一组一组结果。本地结果不等远程。
import type { Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'
import type { RecentEntry } from './recents'
import type { ResultGroup, ResultRow } from './results'
import type { PaletteItem, SourceContext } from './sources'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { buildResults, remoteSources, splitPrefix } from './results'
import { paletteSources } from './sources'

import { routeIds } from '@/lib/addresses'
import { useWorkspaceStore } from '@/stores/workspace'

/** 停止打字多久之后才去问远程数据源。 */
const SEARCH_DELAY_MS = 200

export interface PaletteSearchOptions {
  /** 输入框里现在是什么。 */
  input: Ref<string>
  /** 记下来的「最近去过」。 */
  recents: Ref<RecentEntry[]>
  /** 面板开着没有：关着的时候不搜，也不列东西。 */
  open: Ref<boolean>
  route: RouteLocationNormalizedLoaded
  router: Router
}

export function usePaletteSearch(options: PaletteSearchOptions) {
  const { input, recents, open, route, router } = options

  // 在哪个项目里找。默认跟着当前页；空着按退格去掉范围（跨项目只找名字），在一个项目
  // 上按 Tab 进到那个项目里。每次打开都回到当前页所在的项目。
  const routeCtx = computed<SourceContext>(() => ({
    projectId: routeIds(route.params).projectId ?? null,
    router,
  }))
  const scope = ref<string | null | undefined>(undefined)
  const ctx = computed<SourceContext>(() =>
    scope.value === undefined ? routeCtx.value : { projectId: scope.value, router }
  )
  const store = useWorkspaceStore()
  const scopeName = computed(() =>
    ctx.value.projectId ? store.projects.find((project) => project.id === ctx.value.projectId)?.name : undefined
  )

  // 「最近去过」不列你现在就在的地方。
  const recentElsewhere = computed(() =>
    recents.value.filter((entry) => {
      try {
        return router.resolve(entry.to).path !== route.path
      } catch {
        return false
      }
    })
  )

  // 远程搜：停止打字一会儿再问，问回来时输入已经变了就扔掉。本地结果不等它。
  const remote = ref(new Map<string, PaletteItem[]>())
  const searching = ref(false)
  let asked = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  watch([input, () => ctx.value.projectId, open], () => {
    clearTimeout(timer)
    const ask = ++asked
    remote.value = new Map()
    // 内容只在一个项目里搜。
    const targets = open.value && ctx.value.projectId ? remoteSources(input.value, paletteSources) : []
    searching.value = targets.length > 0
    if (!targets.length) return
    const text = splitPrefix(input.value).query
    timer = setTimeout(async () => {
      const found = await Promise.all(
        targets.map((source) => source.search!(text, ctx.value).catch(() => [] as PaletteItem[]))
      )
      if (ask !== asked) return
      remote.value = new Map(targets.map((source, i) => [source.id, found[i]]))
      searching.value = false
    }, SEARCH_DELAY_MS)
  })
  onBeforeUnmount(() => clearTimeout(timer))

  const groups = computed<ResultGroup[]>(() =>
    open.value ? buildResults(input.value, paletteSources, ctx.value, recentElsewhere.value, remote.value) : []
  )
  const rows = computed<ResultRow[]>(() => groups.value.flatMap((group) => group.rows))

  return { scope, ctx, scopeName, groups, rows, searching }
}
