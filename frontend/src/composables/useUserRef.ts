// 一句话里提到的那个人：显示名从哪来、点了去哪、怎么去。全在这里。
//
// 和展示那一半（components/common/UserRef.vue）分家的理由：那颗 chip 扇入 48，
// 而它原先自己读名册、自己推路由，于是每一个用到它的组件都被拖进「必须装 store +
// 装路由」的测试/演示环境。现在查询和跳转在这一层，展示组件只认 props。
import { computed, getCurrentInstance, type MaybeRefOrGetter, toValue } from 'vue'

import { memberName } from '@/lib/agentNames'
import { userRefRoute, type UserRefTarget } from '@/lib/userRef'
import { useWorkspaceStore } from '@/stores/workspace'

/**
 * @param handle 认人的唯一依据。为空就没有去处。
 * @param projectId 不传（undefined）时取当前路由上的 projectId：在项目里就去项目里的
 *   成员页。传 null 表示这句话不属于眼下这个项目（例如全局通知），去个人主页。
 */
export function useUserRef(
  handle: MaybeRefOrGetter<string | null | undefined>,
  projectId?: MaybeRefOrGetter<string | null | undefined>
) {
  // 路由和 pinia 都从 app 上拿，而不是 useRouter()/useRoute()：这颗 chip 散落在各处，
  // 有的所在树没装路由（孤立渲染的卡片），那里照样画 @名字，只是没有去处。
  const app = getCurrentInstance()?.appContext.config.globalProperties

  // 名册在项目框里才有，调用方给了名字就不用它。
  const store = app?.$pinia ? useWorkspaceStore() : null

  const label = computed(() => {
    const h = toValue(handle)
    if (!h) return ''
    const row = store?.members.find((m) => m.user_handle === h)
    return memberName(row) || h
  })

  const to = computed<UserRefTarget | null>(() => {
    const h = toValue(handle)
    if (!h || !app?.$router) return null
    const given = projectId === undefined ? undefined : toValue(projectId)
    const pid = given !== undefined ? given : (app.$route?.params?.projectId as string | undefined)
    return userRefRoute(h, pid)
  })

  /** 跳。没有去处就什么也不做——模板那边本来也没给它挂点击。 */
  function navigate(): void {
    const target = to.value
    if (!target) return
    void app?.$router.push(target)
  }

  return { label, to, navigate }
}
