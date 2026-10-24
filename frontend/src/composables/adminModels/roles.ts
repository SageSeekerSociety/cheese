// 平台模型角色那一段：读每个角色现在跑在哪个模型上，把一个角色指到另一个模型。
//
// 指完要连模型表一起重拉：每个模型上挂着哪些角色（`roles`）是模型表那一段在画，
// 停用、删除前那道提醒也读它。读失败留在这一段自己的位置上；写失败落到页顶那条横条。
import type { PlatformRole } from '@/api/admin/gateway'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { type AdminModelsBanner, type AdminModelsReload, message } from './shared'

import { getPlatformRoles, setPlatformRole } from '@/api'
import { roleLabelKey } from '@/lib/platformRoles'

export interface AdminModelsRolesHost extends AdminModelsBanner, AdminModelsReload {}

export function useAdminModelsRoles(host: AdminModelsRolesHost) {
  const { t } = useI18n()
  const { writeError, notice, load } = host

  const roles = ref<PlatformRole[] | null>(null)
  const rolesLoading = ref(false)
  const rolesError = ref<string | null>(null)
  /** 正在写的那个角色：同一时刻只改一个，那一行的下拉灰掉。 */
  const roleBusy = ref<string | null>(null)

  async function loadRoles() {
    rolesLoading.value = true
    rolesError.value = null
    try {
      roles.value = (await getPlatformRoles()).roles
    } catch (e) {
      roles.value = null
      rolesError.value = message(e, t('models.page.loadFailed'))
    } finally {
      rolesLoading.value = false
    }
  }

  /** `model` 为 null：回到部署的设置项。 */
  async function setRole(role: string, model: string | null) {
    roleBusy.value = role
    writeError.value = null
    try {
      await setPlatformRole(role, model)
      notice.value = t('models.roles.notice.set', { role: t(roleLabelKey(role)) })
      await Promise.all([loadRoles(), load()])
    } catch (e) {
      writeError.value = message(e, t('models.roles.failed'))
    } finally {
      roleBusy.value = null
    }
  }

  return { roles, rolesLoading, rolesError, roleBusy, loadRoles, setRole }
}
