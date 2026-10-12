// 个人主页 / 成员页一域的形状（spec §1、§7.2、§8.4），从 `@/cx_types` 里整块搬来
// 的一节：cx_types.ts 顶在自己的行数上限上，只能变短。名字照旧从 `@/cx_types`
// 转出去，所以引它的人一个都不用改。
import type { Topic, TopicRef } from '@/cx_types'

// A topic the member started, shown on their member page.
export type MemberTopic = Pick<Topic, 'id' | 'title' | 'status'>

// GET /api/projects/{id}/members/{handle}/summary
export interface MemberSummary {
  handle: string
  /** 他在这个项目里的来路：所有者、团队成员、外部成员、AI 队友（带 instance_handle）；查不到是 null。 */
  source?: 'owner' | 'team' | 'external' | 'agent' | null
  topics_started: MemberTopic[]
  topics_active?: MemberTopic[]
  weekly_contributions?: number
  waiting_on_you: TopicRef[]
  [key: string]: unknown
}

/** How the person is in a project: its owner, through its team, invited alone, or as its AI teammate. */
export type ProfileProjectRole = 'owner' | 'team' | 'external' | 'agent'

// One project the person is in, as the viewer may see it.
export interface ProfileProject {
  project_id: string
  name: string
  source: ProfileProjectRole
  topics_started: number
  contributions: number
  /** Their newest contribution there; null when they have none. */
  last_active_at: string | null
  /** Contributions in the last 12 trailing 7-day spans, oldest first. */
  weekly: number[]
}

// A team the person is in that the viewer may see (the TeamSummary payload).
export interface ProfileTeam {
  id: number
  /** Null only for a team that no longer exists. */
  handle: string | null
  name: string
  intro: string
  avatarId: number | null
}

/** One UTC day of the activity year. `date` is `YYYY-MM-DD`. */
export interface ProfileActivityDay {
  date: string
  count: number
}

// One thing an agent noted about this person, and where it was noted. The
// project is null once the person can no longer read it.
export interface ProfileUnderstanding {
  id: string
  content: string
  created_at: string
  project_id: string | null
  project_name: string | null
  agent_handle: string | null
  agent_name: string | null
  agent_name_source?: string | null
}

// GET /api/users/{handle}/profile — cut to what the viewer may see.
// `understanding` = what 芝士 has learned about this person (个人记忆, §8.4);
// only the person themselves receives it, everyone else gets [].
export interface UserProfile {
  handle: string
  name: string
  bio: string
  /** The raw profile avatar, which may be the platform default (see useChosenAvatar). */
  avatar_id: number | null
  /** Null when no account holds this handle. */
  joined_at: string | null
  teams: ProfileTeam[]
  /** The last 365 UTC days, oldest first, today last. */
  activity: { days: ProfileActivityDay[]; total: number }
  projects: ProfileProject[]
  understanding: ProfileUnderstanding[]
}

// GET /api/users/{handle}/topics — a topic the person wrote in.
export interface ProfileTopic {
  id: string
  title: string
  status: string
  project_id: string
  project_name: string
  /** Their contributions in it, inside the requested dates when given. */
  contributions: number
  last_participated_at: string
}
