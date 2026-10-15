// 手机顶栏中间那一格（MobileAppBar 的 #app-bar-slot）此刻有没有页面在填。
//
// 由填的那个组件自己说：它把头部 Teleport 进去的同时在这里登记，顶栏据此不写路由的
// 标题。以前是路由声明（meta.barSlot），页面和路由表两处得对得上，那一格也跟着路由
// 出现、消失；支线那一层漏了声明，手机上它的头部就没处挂，往回走时 Vue 卸它卸到一半
// 出错，之后每次换页都停在原地。现在那一格一直在顶栏里，谁填谁登记。
//
// 登记发生在组件 setup 里，和顶栏同一次渲染、浏览器画出来之前：第一帧就是对的。
import { computed, onActivated, onDeactivated, onScopeDispose, ref, watch } from 'vue'

const claims = ref(0)

/** 有组件正往顶栏那一格里填东西。 */
export const appBarClaimed = computed(() => claims.value > 0)

/** 这个组件在 `active()` 为真时往顶栏那一格里填东西。组件卸下、被保活停用时一并撤销。 */
export function claimAppBar(active: () => boolean): void {
  const live = ref(true)
  let held = false
  const hold = (on: boolean) => {
    if (on === held) return
    held = on
    claims.value += on ? 1 : -1
  }
  watch(() => live.value && active(), hold, { immediate: true, flush: 'sync' })
  onDeactivated(() => (live.value = false))
  onActivated(() => (live.value = true))
  onScopeDispose(() => hold(false))
}
