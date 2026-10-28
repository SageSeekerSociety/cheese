// 「导出项目」的动作与状态（docs/project-export.md）。
//
// 动作留在这一层，不放进 `components/settings/ProjectExportSection.vue`：`src/components`
// 下的组件只从 props 画、只往外 emit，不许碰 API 层（guards 的 import-boundary）。
// 这一块和分支保护一个路子——组件是哑的，取数与发请求在 composable 里，页面接线。
//
// 后端把整个项目现算成一份 tar，大项目要等一会儿才产出，所以按下去进 loading、并禁用，
// 免得连点发两遍。成没成由 `downloadFile` 那一层弹一句（`lib/download`）。
import { ref } from 'vue'

import { downloadFile } from '@/api'
import { projectExportUrl } from '@/lib/projectExport'

export function useProjectExport(projectId: () => string, projectName: () => string) {
  const exporting = ref(false)
  async function exportProject() {
    exporting.value = true
    try {
      // 成功与失败各弹一句，都在 `downloadFile` 那一层（`lib/download`）。
      await downloadFile(projectExportUrl(projectId()), `${projectName() || projectId()}.tar`)
    } finally {
      exporting.value = false
    }
  }

  return { exporting, exportProject }
}
