// 这一页此刻是不是在屏幕上。保活的页面（App.vue 的 keptAlivePages）离开后组件还在，
// 它的查询也还算「有人在看」：别处一标过期它就跟着重读。所以保活页面的查询把
// `enabled` 绑到这里，离开时停下，回来时过期了的再读一次。
import { onActivated, onDeactivated, ref } from 'vue'

export function usePageActive() {
  const active = ref(true)
  onActivated(() => (active.value = true))
  onDeactivated(() => (active.value = false))
  return active
}
