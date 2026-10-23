// 一件任务自己的链接：贴到别处、别人点开能到同一页。
//
// 单独一份，不放进 `taskActions.ts`：那一份要给任务页头这类「只凭 props 画」的组件
// 读，而算链接要 router、`linkOf` 又会把 `@/lib/addresses` 带进组件的依赖图（它够得
// 着接口层），组件就会从 A 掉到 C。要复制链接的那几处（页面、侧栏）自己到这里取。
import type { Router } from 'vue-router'
import type { TaskActionTask } from './taskActions'

import { copyLink, linkOf } from './copy'

export function copyTaskLink(router: Router, task: TaskActionTask): void {
  void copyLink(
    linkOf(router, {
      name: 'workspace-task',
      params: { projectId: task.project_id, topicId: task.room_id, taskId: task.id },
    })
  )
}
