// 「角色和权限」那张表的数据和动作（#3830）：读 `GET /projects/{id}/roles`，勾一格存一次，
// 「恢复默认」删掉项目改过的那几行。存不上就把那一格退回去，并说一句。
import type { ProjectRole } from '@/lib/projectPermissions'

import { ref, watch } from 'vue'

import { getRoleGrants, resetRoleGrants, setRoleGrants } from '@/api'
import { isLocked } from '@/lib/projectPermissions'

function empty(): Record<ProjectRole, string[]> {
  return { admin: [], member: [], mentor: [] }
}

export function useProjectRoles(projectId: () => string, options: { enabled?: () => boolean } = {}) {
  const enabled = options.enabled ?? (() => true)
  const grants = ref<Record<ProjectRole, string[]>>(empty())
  const isDefault = ref(true)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    const id = projectId()
    loading.value = true
    error.value = null
    try {
      const got = await getRoleGrants(id)
      if (id !== projectId()) return
      grants.value = got.grants
      isDefault.value = !got.changed
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }
  watch(
    () => [projectId(), enabled()] as const,
    ([, on]) => {
      if (on) void load()
    },
    { immediate: true }
  )

  async function toggle(role: ProjectRole, permission: string, granted: boolean) {
    if (isLocked(permission)) return
    const before = grants.value
    const rest = before[role].filter((p) => p !== permission)
    const next = granted ? [...rest, permission] : rest
    grants.value = { ...before, [role]: next }
    error.value = null
    try {
      const saved = await setRoleGrants(projectId(), { [role]: next })
      grants.value = saved.grants
      isDefault.value = !saved.changed
    } catch (e) {
      grants.value = before
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function reset() {
    error.value = null
    try {
      const saved = await resetRoleGrants(projectId())
      grants.value = saved.grants
      isDefault.value = !saved.changed
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  return { grants, isDefault, loading, error, toggle, reset }
}
