/**
 * 预览站里「这一批容器页拆出来的画面」（`catalogScenesPages.ts`）读的数据。
 *
 * 三件画面各有一份自己的形状：空间名录那两块列表（我的申请、名录上的板）、小队的项目、
 * 提问页的小队候选。小队链接落地页直接复用 `catalogSharedFixtures.ts` 的 `teamProfile()`。
 * 全是产品里真会出现的形状 —— 照着真接口的响应造，照着真页面里那几种状态写。
 *
 * 这里只出数据，不引任何运行时依赖：哪一份配哪个组件、每一格看什么，在
 * `catalogScenesPages.ts`。
 */
import type { Project } from '@/cx_types'
import type { Space, SpaceApplication } from '@/types'

/** 画面里那几个回调：预览站上走一趟就完，只看画出来的样子。 */
export function noop(): void {}

export async function noopAsync(): Promise<void> {}

// ---- 空间名录（IndexView）--------------------------------------------------

/** 一块板：`types/spaces.ts` 的 `Space`。只填名录卡片会读到的那几项。 */
export function space(over: Partial<Space> = {}): Space {
  return {
    id: 1,
    intro: '把课件和作业放上来，每周碰一次。',
    name: '操作系统',
    avatarId: 0,
    admins: [],
    taskTemplates: '[]',
    classificationTopics: [],
    ...over,
  } as Space
}

/** 名录上那几块板：名字、简介、头像三样都不一样，一列排得开。 */
export const INDEX_SPACES: Space[] = [
  space({ id: 1, name: '操作系统', intro: '把课件和作业放上来，每周碰一次。', avatarId: 3 }),
  space({ id: 2, name: '算法竞赛', intro: '每周一道真题，写完互相看代码。', avatarId: 5 }),
  space({ id: 3, name: '嵌入式', intro: '从点灯到能跑的系统。', avatarId: 7 }),
  space({ id: 4, name: '编译原理', intro: '一起把课本上的编译器写出来。', avatarId: 11 }),
  space({ id: 5, name: '计算机图形学', intro: '渲染一篇论文里的那张图。', avatarId: 13 }),
  space({ id: 6, name: '数据库系统', intro: '从 B+ 树讲到事务隔离。', avatarId: 17 }),
]

/** 我提交过的申请：等审的、被驳回带原因的、过审的，各一条。 */
export const INDEX_APPLICATIONS: SpaceApplication[] = [
  {
    id: 11,
    avatarId: 5,
    name: '算法竞赛',
    intro: '每周一道真题，写完互相看代码。',
    reviewStatus: 'PENDING',
    description: '',
    owner: 'wangchangxin',
    reviewReason: null,
    reviewedBy: null,
    reviewedAt: null,
    createdAt: '2026-09-21T08:00:00Z',
  },
  {
    id: 12,
    avatarId: 7,
    name: '嵌入式',
    intro: '从点灯到能跑的系统。',
    reviewStatus: 'REJECTED',
    description: '',
    owner: 'wangchangxin',
    reviewReason: '简介里写清这门课是给谁的、每周大概花多少时间。',
    reviewedBy: 'cheese',
    reviewedAt: '2026-09-25T03:12:00Z',
    createdAt: '2026-09-23T08:00:00Z',
  },
  {
    id: 13,
    avatarId: 3,
    name: '操作系统',
    intro: '把课件和作业放上来，每周碰一次。',
    reviewStatus: 'APPROVED',
    description: '',
    owner: 'wangchangxin',
    reviewReason: null,
    reviewedBy: 'cheese',
    reviewedAt: '2026-09-20T03:12:00Z',
    createdAt: '2026-09-18T08:00:00Z',
  },
]

// ---- 小队的项目（TeamProjectsView）-----------------------------------------

/** 一个项目：`cx_types.ts` 的 `Project`。只填那一列卡片会读到的那几项。 */
export function project(over: Partial<Project> = {}): Project {
  return {
    id: 'p-ml',
    name: '机器学习入门',
    slug: 'ml',
    created_at: '2026-03-04T00:00:00Z',
    ...over,
  }
}

/** 这个小队名下的项目。 */
export const TEAM_PROJECTS: Project[] = [
  project(),
  project({ id: 'p-compiler', name: '编译器实验', slug: 'compiler', created_at: '2026-04-18T00:00:00Z' }),
  project({ id: 'p-weekly', name: '周报自动化', slug: 'weekly', created_at: '2026-05-30T00:00:00Z' }),
]
