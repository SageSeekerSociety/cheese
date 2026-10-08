// 外壳给人名 chip 注入的真名册（接口见 lib/userRefDirectory.ts）：名字查 workspace
// store 里的项目成员，去处按眼下的路由算，跳转走路由。
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
    name(handle) {
      return memberName(store.members.find((m) => m.user_handle === handle)) || null
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
