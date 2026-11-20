// AI 队友提议的技能或例行事务那张卡片，点开去哪儿。
//
// 卡片发的是 `open-resource`：还在等人的是 `skill` / `routine`，定下来的是
// `skill-page` / `routine-page`。有右侧面板的地方（频道页）把还在等人的技能开在面板里，
// 读完就地决定；其余的都去它在项目里的那一页。
import type { RouteLocationRaw } from 'vue-router'

const PAGE: Record<string, 'project-skills' | 'project-routines'> = {
  skill: 'project-skills',
  'skill-page': 'project-skills',
  routine: 'project-routines',
  'routine-page': 'project-routines',
}

/** 这个资源是不是一张提议卡发出来的。 */
export function isProposalResource(resource: string): boolean {
  return resource in PAGE
}

/** 一项提议在项目里的那一页。 */
export function proposalPage(resource: string, projectId: string, id: string): RouteLocationRaw | null {
  const name = PAGE[resource]
  if (!name) return null
  return { name, params: { projectId }, query: { [resource.startsWith('skill') ? 'skill' : 'routine']: id } }
}
