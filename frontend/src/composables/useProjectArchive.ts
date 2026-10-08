// 「归档项目」的动作与状态。
//
// 动作留在这一层，不放进 `components/ArchiveProjectDialog.vue`：`src/components` 下的
// 组件只从 props 画、只往外 emit，不许碰 API 层（guards 的 import-boundary）。和
// `useProjectExport` 一个路子——弹窗是哑的，发请求在这里，设置页面接线。
//
// 被拒时把那句理由留着给弹窗画（弹窗不关）；成了之后先把 busy 归位（弹窗据此关掉），
// 再交给 `onArchived` 收尾：刷新项目清单、离开这个项目——它已经不在任何列表里了。
import { ref } from 'vue'

import { archiveProject as archiveProjectApi } from '@/api'
import { t } from '@/i18n'

export function useProjectArchive(projectId: () => string, onArchived: () => Promise<unknown> | void) {
  const archiving = ref(false)
  const archiveError = ref('')

  async function archiveProject() {
    archiveError.value = ''
    archiving.value = true
    try {
      await archiveProjectApi(projectId())
    } catch (e) {
      archiveError.value = e instanceof Error ? e.message : t('work.projectSettings.archive.dialog.failed')
      archiving.value = false
      return
    }
    archiving.value = false
    await onArchived()
  }

  return { archiving, archiveError, archiveProject }
}
