/**
 * 预览站「题目板拆出来的那半张画面」那一组（`catalogScenesSpaces.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着 `@/types` 里那几个类型和各自的 `.spec.ts` 造：分类、
 * 题目模板、域名组、邀请码都照 `types/spaces.ts`，待审核的题目照 `types/tasks.ts` 的
 * `Task`（`AuditTask.spec.ts` 里那份假回包就是这个形状），话题就是 `types/commons.ts`
 * 的 `Topic`。
 *
 * 这里只出数据（外加几个把参数拼成 props 的小函数），不引运行时要装的东西：哪一份配
 * 哪个组件、每一格看什么，在 `catalogScenesSpaces.ts`。
 */
import type { UserRefTarget } from '@/lib/userRef'
import type { Topic } from '@/types'
import type { DomainGroup, SpaceCategory, SpaceInviteCode, SpaceTaskTemplate } from '@/types/spaces'
import type { Task } from '@/types/tasks'

// ---- 分类（ManageCategoriesView）-------------------------------------------

/** 一个分类：`types/spaces.ts` 的 `SpaceCategory`。`archivedAt` 非空就是归档了。 */
export function spaceCategory(over: Partial<SpaceCategory> = {}): SpaceCategory {
  return {
    id: 1,
    name: '算法基础',
    description: '入门题，先修这一门再看别的',
    displayOrder: 1,
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    archivedAt: null,
    ...over,
  }
}

// ---- 题目模板（ManageTemplatesView）----------------------------------------

/** 一道题目模板：`types/spaces.ts` 的 `SpaceTaskTemplate`。 */
export function spaceTemplate(over: Partial<SpaceTaskTemplate> = {}): SpaceTaskTemplate {
  return {
    name: '个人题',
    description: '一个人一道题，交一份代码',
    title: '',
    content: '',
    submitterType: 'USER',
    rank: 1,
    defaultDeadline: 7,
    requireRealName: true,
    ...over,
  }
}

// ---- 域名组（ManageDomainGroupsView）---------------------------------------

/** 一个域名组：`types/spaces.ts` 的 `DomainGroup`。 */
export function domainGroup(over: Partial<DomainGroup> = {}): DomainGroup {
  return {
    id: 1,
    spaceId: 8,
    name: '本校',
    description: '校内邮箱都能报名',
    domains: ['school.edu.cn'],
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    ...over,
  }
}

// ---- 邀请码（InviteCodesView）----------------------------------------------

/** 一张邀请码：`types/spaces.ts` 的 `SpaceInviteCode`。 */
export function inviteCode(over: Partial<SpaceInviteCode> = {}): SpaceInviteCode {
  return {
    id: 1,
    spaceId: 8,
    code: 'K7PM3QXW',
    maxUses: 50,
    useCount: 12,
    expiresAt: null,
    createdAt: 1757000000000,
    note: '十月这批同学',
    createdBy: { id: 1, username: 'cheese', nickname: '芝士', avatarId: null, intro: '' },
    ...over,
  }
}

/** 「谁建的码」那一颗的去处：容器按 handle 算好，画面只负责画。 */
export function inviteRefTo(item: SpaceInviteCode): UserRefTarget | null {
  return item.createdBy ? { path: `/spaces/8/members/${item.createdBy.username}` } : null
}

// ---- 话题（ManageTopicsView / TopicSelectorView）---------------------------

/** 一个分类话题：`types/commons.ts` 的 `Topic`。 */
export function topic(over: Partial<Topic> = {}): Topic {
  return { id: 1, name: '动态规划', ...over }
}

/** 选择器里搜出来的候选：同一个 `Topic` 形状，外加一条新建。 */
export function topicCandidates(query = ''): { id: number; name: string }[] {
  const all = [topic({ id: 1, name: '动态规划' }), topic({ id: 2, name: '图论' })]
  return query ? all.filter((t) => t.name.includes(query)) : all
}

// ---- 待审核题目（AuditTaskView）--------------------------------------------

/** 一道待审核的题目：`types/tasks.ts` 的 `Task`。只填画面会读到的那几项。 */
export function auditTask(over: Partial<Task> = {}): Task {
  return {
    id: 101,
    name: '最短路径',
    intro: '随便一句简介',
    submitterType: 'USER',
    creator: {
      id: 1,
      username: 'li-laoshi',
      nickname: '李老师',
      avatarId: 0,
      intro: '',
      question_count: 0,
      answer_count: 0,
    },
    deadline: null,
    participantLimit: 0,
    defaultDeadline: 7,
    resubmittable: false,
    editable: false,
    description: '',
    submissionSchema: [{ prompt: '提交文件', type: 'FILE' }],
    participants: { total: 0, approved: 0, examples: [] },
    submittable: false,
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    rank: 1,
    approved: 'NONE',
    requireRealName: false,
    ...over,
  }
}
