// 一句话里提到的那个人：显示名从哪来、点了去哪、怎么去。全在这里。
//
// 和展示那一半（components/common/UserRef.vue）分家的理由：那颗 chip 扇入 48，
// 而它原先自己读名册、自己推路由，于是每一个用到它的组件都被拖进「必须装 store +
// 装路由」的测试/演示环境。现在名册和跳转都从注入的 UserRefDirectory 来
// （lib/userRefDirectory.ts），外壳注入真的，没注入时只画名字。
import type { UserRefTarget } from '@/lib/userRef'
import type { UserRefIdentity } from '@/lib/userRefDirectory'

import { computed, inject, type MaybeRefOrGetter, toValue } from 'vue'

import { isAgentHandle } from '@/lib/authorship'
import { OFFLINE_USER_REF_DIRECTORY, USER_REF_DIRECTORY } from '@/lib/userRefDirectory'

/**
 * @param handle 认人的唯一依据。为空就没有去处。
 * @param projectId 不传（undefined）时取当前所在的项目：在项目里就去项目里的成员页。
 *   传 null 表示这句话不属于眼下这个项目（例如全局通知），去个人主页。
 */
export function useUserRef(
  handle: MaybeRefOrGetter<string | null | undefined>,
  projectId?: MaybeRefOrGetter<string | null | undefined>
) {
  const directory = inject(USER_REF_DIRECTORY, OFFLINE_USER_REF_DIRECTORY)

  // 从名册问这个人的身份（名字、挑过的头像、是不是队友）。名册不认得这个人（切到了别的
  // 项目、这个人离开了项目/团队）时退回 handle + 彩色首字母；此时「是不是队友」按
  // handle 命名规则兜底（Design §3.14）——只认得出队友，一个离开名册的 AI 不会被
  // 画成真人；认不出的人一律当人。
  const identity = computed<UserRefIdentity>(() => {
    const h = toValue(handle)
    const known = h ? directory.identity(h) : null
    return known ?? { name: null, avatarId: null, agent: isAgentHandle(h || '') }
  })

  /** 只画名字的那些地方（纯文字的一行，装不下 chip）：名字，查不到就画 handle。 */
  const label = computed(() => identity.value.name || toValue(handle) || '')

  const to = computed<UserRefTarget | null>(() => {
    const h = toValue(handle)
    if (!h) return null
    return directory.target(h, projectId === undefined ? undefined : toValue(projectId))
  })

  /** 跳。没有去处就什么也不做——模板那边本来也没给它挂点击。 */
  function navigate(): void {
    const target = to.value
    if (target) directory.navigate(target)
  }

  return { identity, label, to, navigate }
}
