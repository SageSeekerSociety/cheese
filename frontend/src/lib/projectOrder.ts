import type { Project } from '@/cx_types'

// rail 上项目的先后是**这个人**的排法，所以和 projectCache 一样按 handle 存。
// 整个浏览器一份的 `cheesex.layout` 装不了它：那份不分账号，换个账号进来 rail
// 就按上一个人的顺序排（workspace store 里 lastProjectId 那条注释记着同一个坑）。
//
// 用 localStorage 而不是 projectCache 的 sessionStorage：那份是缓存，关掉标签页
// 就该没了；排法是设置，得留着。
const ORDER_PREFIX = 'cheesex.projectOrder.v1:'

/** 拖到目标格子的哪一边。由指针落在目标上半还是下半决定，所以首尾两个位置都够得着。 */
export type DropEdge = 'before' | 'after'

function key(handle: string): string | null {
  const normalized = handle.trim()
  return normalized ? `${ORDER_PREFIX}${encodeURIComponent(normalized)}` : null
}

export function loadProjectOrder(handle: string): string[] {
  const storageKey = key(handle)
  if (!storageKey || typeof localStorage === 'undefined') return []
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter((id): id is string => typeof id === 'string')
  } catch {
    return [] // malformed stored order
  }
}

export function saveProjectOrder(handle: string, ids: string[]): void {
  const storageKey = key(handle)
  if (!storageKey || typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(storageKey, JSON.stringify(ids))
  } catch {
    // 存不下（隐私模式、配额满）就只有这一次会话算数，rail 仍是拖完的样子。
  }
}

/**
 * 把服务端清单排成 rail 要画的顺序。
 *
 * 没排过的在前、排过的在后。服务端给的是 created_at desc——新项目在最上面——没人
 * 拖过的时候 rail 本来就该是这个样子；拖过之后，新建的那个仍旧出现在最上面，而
 * 不用去一列项目的末尾找它。存过但已经不在清单里的 id（退出了、删掉了）直接丢，
 * 不占位。
 */
export function applyProjectOrder(projects: Project[], order: string[]): Project[] {
  const placed = new Map(order.map((id, i) => [id, i]))
  const ranked = projects.filter((p) => placed.has(p.id))
  ranked.sort((a, b) => (placed.get(a.id) ?? 0) - (placed.get(b.id) ?? 0))
  return [...projects.filter((p) => !placed.has(p.id)), ...ranked]
}

/**
 * 把 `movedId` 插到 `targetId` 的前面或后面，返回新的完整顺序。
 *
 * 返回的是完整的一份，不是只记被拖的那个：下次进来所有项目都是「排过的」，顺序
 * 就照抄，只有这之后新建的项目才算没排过。
 */
export function reorderProjects(projects: Project[], movedId: string, targetId: string, edge: DropEdge): string[] {
  const all = projects.map((p) => p.id)
  if (movedId === targetId || !all.includes(movedId) || !all.includes(targetId)) return all
  const rest = all.filter((id) => id !== movedId)
  const at = rest.indexOf(targetId)
  rest.splice(edge === 'before' ? at : at + 1, 0, movedId)
  return rest
}
