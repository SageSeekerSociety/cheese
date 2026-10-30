// 项目的角色（#3830、#4795）：读 `GET /projects/{id}/roles`，新增、改名、删除，勾一项权限
// 存一次。存不上就把那一项退回去，并说一句。
import type { ProjectRoleRow } from '@/api'

import { ref, watch } from 'vue'

import { createProjectRole, deleteProjectRole, listProjectRoles, updateProjectRole } from '@/api'
import { isLocked } from '@/lib/projectPermissions'

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function useProjectRoles(projectId: () => string, options: { enabled?: () => boolean } = {}) {
  const enabled = options.enabled ?? (() => true)
  const roles = ref<ProjectRoleRow[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let version = 0

  async function load() {
    const id = projectId()
    const current = ++version
    loading.value = true
    error.value = null
    try {
      const got = await listProjectRoles(id)
      if (current !== version || id !== projectId()) return
      roles.value = got.roles
    } catch (e) {
      if (current === version) error.value = messageOf(e)
    } finally {
      if (current === version) loading.value = false
    }
  }
  watch(
    () => [projectId(), enabled()] as const,
    ([, on]) => {
      if (on) void load()
    },
    { immediate: true }
  )

  function replace(row: ProjectRoleRow) {
    roles.value = roles.value.map((r) => (r.key === row.key ? row : r))
  }

  async function save(key: string, permissions: string[]) {
    const before = roles.value
    roles.value = before.map((r) => (r.key === key ? { ...r, permissions } : r))
    error.value = null
    try {
      replace(await updateProjectRole(projectId(), key, { permissions }))
    } catch (e) {
      roles.value = before
      error.value = messageOf(e)
    }
  }

  async function toggle(key: string, permission: string, granted: boolean) {
    const role = roles.value.find((r) => r.key === key)
    if (!role || isLocked(permission)) return
    const rest = role.permissions.filter((p) => p !== permission)
    await save(key, granted ? [...rest, permission] : rest)
  }

  async function reset(key: string) {
    const role = roles.value.find((r) => r.key === key)
    if (role?.defaults) await save(key, role.defaults)
  }

  /** 新增一个角色，返回它；失败时返回 null，`error` 里说为什么。 */
  async function create(name: string | null, from: string): Promise<ProjectRoleRow | null> {
    error.value = null
    try {
      const made = await createProjectRole(
        projectId(),
        from === 'template:mentor' ? { name, template: 'mentor' } : { name, copy_from: from }
      )
      roles.value = [...roles.value, made]
      return made
    } catch (e) {
      error.value = messageOf(e)
      return null
    }
  }

  async function rename(key: string, name: string): Promise<boolean> {
    error.value = null
    try {
      replace(await updateProjectRole(projectId(), key, { name }))
      return true
    } catch (e) {
      error.value = messageOf(e)
      return false
    }
  }

  async function remove(key: string, moveTo?: string): Promise<boolean> {
    error.value = null
    try {
      await deleteProjectRole(projectId(), key, moveTo)
      await load()
      return true
    } catch (e) {
      error.value = messageOf(e)
      return false
    }
  }

  return { roles, loading, error, load, toggle, reset, create, rename, remove }
}
