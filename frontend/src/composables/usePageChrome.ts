// 外壳给页头注入的真东西（接口见 lib/pageChrome.ts）：面包屑按眼下的路由算，操作区
// 和页签取 navigation store 里页面登记的那一份。只在 App.vue 调一次。
import { provide } from 'vue'
import { storeToRefs } from 'pinia'

import { useBreadcrumb } from '@/composables/useBreadcrumb'

import { PAGE_CHROME } from '@/lib/pageChrome'
import { useNavigationStore } from '@/stores/navigation'

export function providePageChrome(): void {
  const { breadcrumbItems } = useBreadcrumb()
  const { actionsComponent, tabsComponent } = storeToRefs(useNavigationStore())
  provide(PAGE_CHROME, { breadcrumbs: breadcrumbItems, actions: actionsComponent, tabs: tabsComponent })
}
