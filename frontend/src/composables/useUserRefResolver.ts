// 一列名字共用的「这一个人叫什么、点了去哪」。
//
// 单个人的那一份是 `composables/useUserRef.ts`：一个组件在 setup 里对着一颗 chip 调
// 一次。可列表里每一行都要一份，而 composable 不能在渲染里按行调，于是这里在 setup 里
// 把名册与去处一次接好，交回一个**纯函数** —— 给 handle，还 `{ name, to }`，并替你把
// 跳转做掉。展示那一半还是 `components/common/UserRef.vue`（只认 props）。
//
// 只给「页里满屏都是人名」的容器用；只有一颗 chip 的地方仍走 useUserRef。
import { getCurrentInstance } from 'vue'

import { memberName } from '@/lib/agentNames'
import { userRefRoute, type UserRefTarget } from '@/lib/userRef'
import { useWorkspaceStore } from '@/stores/workspace'

export interface ResolvedUserRef {
  name: string
  to: UserRefTarget | null
}

export function useUserRefResolver() {
  // 路由和 pinia 都从 app 上拿，而不是 useRouter()/useRoute()：跟 useUserRef 一样，
  // 没装路由的树里照样画 @名字，只是没有去处。
  const app = getCurrentInstance()?.appContext.config.globalProperties
  const store = app?.$pinia ? useWorkspaceStore() : null

  function resolve(handle: string | null | undefined): ResolvedUserRef {
    if (!handle || !app?.$router) return { name: handle ?? '', to: null }
    const row = store?.members.find((m) => m.user_handle === handle)
    const pid = app.$route?.params?.projectId as string | undefined
    return { name: memberName(row) || handle, to: userRefRoute(handle, pid) }
  }

  /** 跳。没有去处就什么也不做——模板那边本来也没给它挂点击。 */
  function navigate(target: UserRefTarget | null): void {
    if (!target) return
    void app?.$router?.push(target)
  }

  return { resolve, navigate }
}
