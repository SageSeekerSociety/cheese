// 页头（components/common/PageHeader.vue）从外壳拿的三样东西：面包屑、页面登记的
// 操作区组件、页面登记的页签组件。
//
// 为什么是注入口：面包屑从路由表算（composables/usePageTitle 读 useRoute），操作区和
// 页签在 navigation store 里。页头自己去拿，它和渲染它的每一页就离不开路由和 pinia；
// 页头被八个还没拆完的页面直接渲染。外壳在 App.vue 注入真的
// （composables/usePageChrome.ts）；没人注入时是 EMPTY_PAGE_CHROME：没有面包屑、
// 没有登记的操作区，页头照样画自己的标题和插槽。
//
// 这里只放类型、键和兜底，不 import 路由或 store。
import type { Component, InjectionKey, Ref } from 'vue'
import type { BreadcrumbItem } from '@/types/title'

import { ref, shallowRef } from 'vue'

export interface PageChrome {
  breadcrumbs: Readonly<Ref<BreadcrumbItem[]>>
  /** 页面用 navigation store 的 setActions 登记的操作区。 */
  actions: Readonly<Ref<Component | null>>
  /** 页面用 setTabs 登记的页签。 */
  tabs: Readonly<Ref<Component | null>>
}

export const EMPTY_PAGE_CHROME: PageChrome = {
  breadcrumbs: ref([]),
  actions: shallowRef(null),
  tabs: shallowRef(null),
}

export const PAGE_CHROME: InjectionKey<PageChrome> = Symbol('pageChrome')
