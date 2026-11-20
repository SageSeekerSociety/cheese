// 项目的角色和权限（#3830、#4795）。所有者在最上面，拥有全部权限，不算角色；管理员、成员
// 两个角色是内置的，名字固定；别的角色是项目自己的，名字和权限都由项目定，导师是其中照模板
// 建的一种。这里只有权限的名字、分组和排列顺序，与后端 `project_permissions.py` 同名；谁有
// 哪一项、默认是什么由后端说。
import { t } from '@/i18n'

/** 角色叫什么：内置角色和没改过名的模板角色用界面上的固定名字，别的用项目起的名字。 */
export function roleName(role: { name: string | null; builtin: string | null; template: string | null }): string {
  if (role.name) return role.name
  const fixed = role.builtin ?? role.template
  return fixed ? t(`work.projectSettings.roles.role.${fixed}`) : ''
}

export type PermissionGroup = 'people' | 'project' | 'channels' | 'conversation' | 'content' | 'agents'

export interface PermissionDef {
  key: string
  group: PermissionGroup
}

export const PERMISSIONS: PermissionDef[] = [
  { key: 'manage_members', group: 'people' },
  { key: 'change_roles', group: 'people' },
  { key: 'edit_settings', group: 'project' },
  { key: 'export_project', group: 'project' },
  { key: 'create_channels', group: 'channels' },
  { key: 'manage_channels', group: 'channels' },
  { key: 'speak', group: 'conversation' },
  { key: 'ask_agent', group: 'conversation' },
  { key: 'tasks', group: 'conversation' },
  { key: 'edit_content', group: 'content' },
  { key: 'share_documents', group: 'content' },
  { key: 'comment', group: 'content' },
  { key: 'configure_agents', group: 'agents' },
]

export const PERMISSION_GROUPS: PermissionGroup[] = [
  'people',
  'project',
  'channels',
  'conversation',
  'content',
  'agents',
]

/** 不能改的格子：管理员永远能管人和角色，别人永远不能，免得项目没人管。 */
export function isLocked(permission: string): boolean {
  return permission === 'manage_members' || permission === 'change_roles'
}

/** 我在这个项目里能不能做这件事：后端给的 `my_permissions` 说了算，这里不自己推。 */
export function mayIn(project: { my_permissions?: string[] } | null | undefined, permission: string): boolean {
  return project?.my_permissions?.includes(permission) ?? false
}
