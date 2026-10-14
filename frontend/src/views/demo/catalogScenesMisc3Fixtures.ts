/** 第四批场景的数据叶子：申请与邀请、学习队列。复用目录里的成员、团队和发言。 */
import type { ProjectInvitation } from '@/cx_types'
import type { TeamMembershipApplication } from '@/types'
import type {
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestion,
  SpaceLearningQueues,
} from '@/types/apiSpaces'

import { EXCERPTS } from './catalogFixtures'
import { MY_TEAMS, user } from './catalogScenesMiscFixtures'

const createdAt = Date.parse('2026-09-28T09:12:00Z')
function application(id: number, type: TeamMembershipApplication['type']): TeamMembershipApplication {
  return {
    id,
    user: user(),
    initiator: user({ id: 2, username: 'bob' }),
    team: { ...MY_TEAMS[id % MY_TEAMS.length], createdAt, updatedAt: createdAt },
    type,
    status: 'PENDING',
    role: 'MEMBER',
    message: null,
    createdAt,
    updatedAt: createdAt,
  }
}

export const PENDING_REQUESTS = [application(1, 'REQUEST')]
export const PENDING_INVITATIONS = [application(2, 'INVITATION')]
export const PROJECT_INVITATIONS: ProjectInvitation[] = [
  {
    id: 'invite-1',
    project_id: 'p1',
    project_name: '光学实验',
    invitee_handle: 'bob',
    inviter_handle: 'alice',
    status: 'pending',
    created_at: '2026-09-28T09:12:00Z',
  },
  {
    id: 'invite-2',
    project_id: 'p2',
    project_name: '',
    invitee_handle: 'bob',
    inviter_handle: 'carol',
    status: 'pending',
    created_at: '2026-09-28T09:12:00Z',
  },
]

export const LEARNING_QUESTIONS: SpaceLearningQuestion[] = EXCERPTS.map((excerpt) => ({
  ...excerpt,
  projectName: '光学实验',
}))
export const LEARNING_FILTERS: SpaceLearningFilters = {
  students: EXCERPTS.map((excerpt) => ({
    handle: excerpt.student,
    name: excerpt.studentName,
    avatarId: excerpt.studentAvatarId ?? null,
    isAgent: excerpt.studentIsAgent ?? false,
  })),
  knowledgePoints: [{ categoryId: 7, name: '瑞利散射' }],
  projectCount: 2,
}
export const LEARNING_QUEUES: SpaceLearningQueues = {
  reviewFlag: { available: false, reason: '尚未记录需要老师复核的信号', items: [] },
  stuckPoints: [
    {
      knowledgePoint: '瑞利散射',
      studentCount: 2,
      projectCount: 2,
      questionCount: 3,
      latestAt: createdAt,
      example: LEARNING_QUESTIONS[0],
    },
  ],
}
export const EMPTY_LEARNING_QUEUES: SpaceLearningQueues = {
  reviewFlag: { available: true, reason: '', items: [] },
  stuckPoints: [],
}
export const LEARNING_OUTLINE: SpaceLearningOutline = {
  title: '光学讲解提纲',
  sections: [
    { knowledgePoint: '瑞利散射', session: 1, excerpts: [EXCERPTS[0]] },
    { knowledgePoint: '未分类', session: 2, excerpts: [EXCERPTS[1]] },
  ],
  missing: ['unavailable-block'],
}
