// 每一种服务器资源在缓存里的名字。
//
// 按「属于谁」分层：`['project', id, …]` 是一个项目的，`['room', id, …]` 是一段对话的
// （频道、任务、私聊都是房间）。分层是为了两件事：
//
// - 一处变了，按前缀把那一片标过期：`['room', id]` 是这个房间的一切。
// - 一份响应可以填好几个 key：频道接口一次带回成员、进行中的任务、置顶时，各自写进
//   自己那一格，读它们的地方不用知道数据是从哪来的。
//
// 同一种资源只有一个 key 和一个读取函数（在 `query/` 下按领域放），别处不再各存一份。
import type { TaskFilter } from '@/api/tasks'

export const keys = {
  /** 我能看到的项目。 */
  projects: () => ['projects'] as const,

  project: (projectId: string) => ['project', projectId] as const,
  projectRow: (projectId: string) => ['project', projectId, 'row'] as const,
  projectTopics: (projectId: string) => ['project', projectId, 'topics'] as const,
  projectMembers: (projectId: string) => ['project', projectId, 'members'] as const,
  projectAgents: (projectId: string) => ['project', projectId, 'agents'] as const,
  projectUnread: (projectId: string, me: string) => ['project', projectId, 'unread', me] as const,
  projectPrivateUnread: (projectId: string, me: string) => ['project', projectId, 'private-unread', me] as const,
  projectNotifyLevels: (projectId: string) => ['project', projectId, 'notify-levels'] as const,
  /** 项目里的任务：前缀标过期时所有筛选一起作废。 */
  projectTasks: (projectId: string) => ['project', projectId, 'tasks'] as const,
  projectOpenTasks: (projectId: string) => ['project', projectId, 'tasks', 'open'] as const,
  projectClosedTasks: (projectId: string, filter: { channel: string | null; whose: TaskFilter }) =>
    ['project', projectId, 'tasks', 'closed', filter] as const,
  projectOverview: (projectId: string) => ['project', projectId, 'overview'] as const,
  projectProgress: (projectId: string) => ['project', projectId, 'progress'] as const,
  projectDocs: (projectId: string, kind: string) => ['project', projectId, 'docs', kind] as const,
  projectSkills: (projectId: string) => ['project', projectId, 'skills'] as const,

  room: (roomId: string) => ['room', roomId] as const,
  /** 房间这一行本身（标题、状态、可见性）。 */
  roomRow: (roomId: string) => ['room', roomId, 'row'] as const,
  roomMembers: (roomId: string) => ['room', roomId, 'members'] as const,
  roomProgress: (roomId: string) => ['room', roomId, 'progress'] as const,
  roomTasks: (roomId: string) => ['room', roomId, 'tasks'] as const,
  roomTaskList: (roomId: string, filter: object) => ['room', roomId, 'tasks', filter] as const,
  /** 任务房间里的那个任务。 */
  roomTask: (roomId: string) => ['room', roomId, 'task'] as const,
  roomPreview: (roomId: string) => ['room', roomId, 'preview'] as const,
  roomPins: (roomId: string) => ['room', roomId, 'pins'] as const,
  roomThreads: (roomId: string) => ['room', roomId, 'threads'] as const,
  roomComputeProfile: (roomId: string) => ['room', roomId, 'compute-profile'] as const,
  /** 这段对话里还活着的反馈提案卡。 */
  roomFeedbackProposals: (roomId: string) => ['room', roomId, 'feedback-proposals'] as const,
  /** 这个房间里 AI 队友提议、还在等人的技能。 */
  roomSkillProposals: (roomId: string) => ['room', roomId, 'skill-proposals'] as const,
  /** 任务从哪来、带着什么（任务页的「相关」）。 */
  taskRelated: (taskId: string) => ['room', taskId, 'related'] as const,
  taskReviewComments: (taskId: string) => ['room', taskId, 'review-comments'] as const,
  /** 对话最新的那一页：打开房间先画它，预取也是取它。 */
  roomNewestBlocks: (roomId: string) => ['room', roomId, 'blocks', 'newest'] as const,
}
