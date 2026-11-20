// 项目设置里的「项目头像」：存一次就把项目清单和这个项目那两份缓存里的头像换掉，
// 侧栏、切换项目的菜单、设置页头不用等重读就是新的样子。
import type { Project } from '@/cx_types'
import type { ProjectAvatarSpec } from '@/utils/projectAvatar'

import { ref } from 'vue'

import { setProjectAvatar } from '@/api'
import { createAvatar } from '@/api/avatars'
import { t } from '@/i18n'
import { patchQuery } from '@/query/client'
import { keys } from '@/query/keys'

export function useProjectAvatar(projectId: () => string) {
  const saving = ref(false)
  const error = ref('')

  function remember(avatar: ProjectAvatarSpec) {
    const id = projectId()
    void patchQuery<Project[]>(keys.projects(), (list) => list.map((p) => (p.id === id ? { ...p, avatar } : p)))
    void patchQuery<Project>(keys.projectRow(id), (p) => ({ ...p, avatar }))
  }

  async function save(next: ProjectAvatarSpec): Promise<boolean> {
    saving.value = true
    error.value = ''
    try {
      remember(await setProjectAvatar(projectId(), next))
      return true
    } catch (e) {
      error.value = e instanceof Error && e.message ? e.message : t('work.projectSettings.avatar.saveFailed')
      return false
    } finally {
      saving.value = false
    }
  }

  async function upload(file: File, hue: number | null): Promise<boolean> {
    saving.value = true
    error.value = ''
    let imageId: number
    try {
      imageId = (await createAvatar(file)).avatarId
    } catch (e) {
      error.value = e instanceof Error && e.message ? e.message : t('work.projectSettings.avatar.uploadFailed')
      saving.value = false
      return false
    }
    return save({ kind: 'image', glyph: null, hue, image_id: imageId })
  }

  return { saving, error, save, upload }
}
