import { computed } from 'vue'
import { useRoute } from 'vue-router'

/**
 * 知是 2.0 内测开关。界面是否升级为「群聊 / 现场」等新功能，完全由 URL 决定：
 * 带 `?exp=true` 的链接进入内测态，普通链接则是未升级的旧版。内测态在应用内导航
 * 时保持粘性（由 `installExperimentalGuard` 把 `exp=true` 带到每一次跳转），因此
 * 「弹来弹去都是内测窗口」；而重新用一个不带该参数的普通链接访问就回到非内测态。
 *
 * 组件用这个 composable 读取当前是否内测态（对当前路由 query 响应式）。
 */
export function useExperimental() {
  const route = useRoute()
  return computed(() => route.query.exp === 'true')
}
