// 外壳给人名 chip 注入的真名册（接口见 lib/userRefDirectory.ts）：身份（名字、挑过
// 的头像、是不是队友）查 workspace store 里的项目成员，去处按眼下的路由算，跳转走路由。
//
// 只在 App.vue 调一次。要在单独挂载的测试里看到真名字和真去处，也可以在那棵树的根上
// 调它，或者直接 provide 一个假的 UserRefDirectory。
import { provide } from 'vue'
import { useRouter } from 'vue-router'

import { memberName } from '@/lib/agentNames'
import { userRefRoute } from '@/lib/userRef'
import { USER_REF_DIRECTORY, type UserRefDirectory } from '@/lib/userRefDirectory'
import { useWorkspaceStore } from '@/stores/workspace'

export function createUserRefDirectory(): UserRefDirectory {
  const router = useRouter()
  const store = useWorkspaceStore()
  return {
    identity(handle) {
      // 名册是眼下打开的这个项目的成员。切到别的项目、或这个人已经离开名册时查不到，
      // 回 null —— 由 useUserRef 定降级：退回 handle + 彩色首字母，是不是队友再按
      // handle 命名规则兜底（Design §3.14）。
      const row = store.members.find((m) => m.user_handle === handle)
      if (!row) return null
      return {
        name: memberName(row) || null,
        avatarId: row.avatar_id ?? null,
        agent: row.agent === true,
      }
    },
    target(handle, projectId) {
      const pid =
        projectId !== undefined ? projectId : (router.currentRoute.value.params.projectId as string | undefined)
      return userRefRoute(handle, pid)
    },
    navigate(target) {
      void router.push(target)
    },
  }
}

export function provideUserRefDirectory(): void {
  provide(USER_REF_DIRECTORY, createUserRefDirectory())
}
