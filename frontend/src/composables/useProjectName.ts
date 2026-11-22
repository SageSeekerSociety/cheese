// 项目设置里的「名称」：存一次就把项目清单和这个项目那两份缓存里的名字换掉，侧栏、
// 切换项目的菜单、设置页头和默认头像上的字不用等重读就跟着变。
import type { Project } from '@/cx_types'

import { ref } from 'vue'

import { renameProject } from '@/api'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'
import { patchQuery } from '@/query/client'
import { keys } from '@/query/keys'

export function useProjectName(projectId: () => string) {
  const saving = ref(false)
  const error = ref('')

  async function save(next: string) {
    saving.value = true
    error.value = ''
    try {
      const { name } = await renameProject(projectId(), next)
      const id = projectId()
      void patchQuery<Project[]>(keys.projects(), (list) => list.map((p) => (p.id === id ? { ...p, name } : p)))
      void patchQuery<Project>(keys.projectRow(id), (p) => ({ ...p, name }))
      toast(t('work.projectSettings.name.saved'))
    } catch (e) {
      error.value = e instanceof Error && e.message ? e.message : t('work.projectSettings.name.failed')
    } finally {
      saving.value = false
    }
  }

  return { saving, error, save }
}
