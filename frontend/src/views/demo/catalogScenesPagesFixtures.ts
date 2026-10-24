/**
 * 预览站里「这一批容器页拆出来的画面」（`catalogScenesPages.ts`）读的数据。
 *
 * 两件画面各有一份自己的形状：小队的项目、提问页的小队候选。小队链接落地页直接复用 `catalogSharedFixtures.ts` 的 `teamProfile()`。
 * 全是产品里真会出现的形状 —— 照着真接口的响应造，照着真页面里那几种状态写。
 *
 * 这里只出数据，不引任何运行时依赖：哪一份配哪个组件、每一格看什么，在
 * `catalogScenesPages.ts`。
 */
import type { Project } from '@/cx_types'

/** 画面里那几个回调：预览站上走一趟就完，只看画出来的样子。 */
export function noop(): void {}

export async function noopAsync(): Promise<void> {}

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
