// 项目的频道，从项目这一侧看：「浏览频道」和项目设置的「频道」一栏读的同一份。
// 公开频道和我在里面的私密频道，带说明、人数、进行中任务；管项目的人另外看得到自己
// 不在里面的私密频道，只有名称、管理者、人数和最近活跃（`visible` 为假）。
export interface ChannelEntry {
  id: string
  title: string
  /** 这是「综合」。 */
  general: boolean
  members_only: boolean
  archived: boolean
  joined: boolean
  /** 我看得到里面：公开频道，或者我在里面的私密频道。 */
  visible: boolean
  description: string | null
  member_count: number
  /** 进行中的任务；看不到里面时为 null。 */
  open_tasks: number | null
  last_activity_at: string | null
  /** 管理者的 handle；「综合」没有。 */
  manager: string | null
  /** 我能改它的名称、说明、成员（在里面才算）。 */
  can_manage: boolean
  /** 我能不进去就归档它、换管理者。 */
  can_administer: boolean
}

export interface ChannelDirectory {
  items: ChannelEntry[]
  manages_project: boolean
}
