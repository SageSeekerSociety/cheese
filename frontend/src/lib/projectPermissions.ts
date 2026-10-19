// 项目的角色和权限（#3830）。所有者在最上面，拥有全部权限，不进这张表；下面三种角色各是
// 一组权限，项目可以在「角色和权限」里改，锁定的两项不能改。这里只有权限的名字、分组和
// 排列顺序，与后端 `project_permissions.py` 同名；谁有哪一项、默认是什么由后端说。

export type ProjectRole = 'admin' | 'member' | 'mentor'

export const PROJECT_ROLES: ProjectRole[] = ['admin', 'member', 'mentor']

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
