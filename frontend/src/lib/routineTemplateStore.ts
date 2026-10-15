// 「我存的」那几条模板：存在这台浏览器上，一张表一个键。
//
// 一条模板是填表的起点，不是规则本身 —— 它没有谁在跑、没有执行记录，删掉也不影响已经
// 建出来的规则。所以它不进后端：多一张表、一次迁移，只为记住几个「人的习惯」。
// 代价是换一台机器就看不到，这一点在设计稿里也是这么画的（模板跟着人走）。
//
// 键名带项目：同一个人的项目各不相同，A 项目里的「每周进展汇总」不该出现在 B 项目的
// 列表里。存储本身可能写不进去（隐私模式、配额满），那种情况按「这一次没记住」算，
// 不抛 —— `browserStorage` 的那两条判据管的就是这件事。
import type { RoutineInput, RoutineTrigger } from './routine'
import type { RoutineTemplate } from './routineTemplates'

import { storageGetJson, storageSetJson } from './browserStorage'

/** 一个项目一个键。 */
function key(projectId: string): string {
  return `cheese:routine-templates:${projectId}`
}

/** 一条记录最长这么多字，超了当作没写：模板里是几句话，写到这么长的不是模板。 */
const MAX_CHARS = 20000

/** 存下来的一条：形状和 `RoutineTemplate` 一样，只是这里要判一遍 —— 浏览器里的东西
 *  是人手能改的，读到不认识的形状就当没存过，不能让它把列表画成一片乱码。 */
function isTemplate(value: unknown): value is RoutineTemplate {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.id === 'string' &&
    typeof v.title === 'string' &&
    typeof v.goal === 'string' &&
    typeof v.trigger === 'string' &&
    !!v.spec &&
    typeof v.spec === 'object'
  )
}

/** 这个项目里人自己存的模板，最近的在前。 */
export function readTemplates(projectId: string): RoutineTemplate[] {
  const raw = storageGetJson<unknown>(key(projectId))
  return Array.isArray(raw) ? raw.filter(isTemplate) : []
}

/** 存下一条模板：名字加目标一起进去。返回存下来的那一条（调用方要它的 id）。 */
export function addTemplate(projectId: string, body: RoutineInput): RoutineTemplate | null {
  const title = body.title.trim() || body.instructions.trim().split('\n')[0] || ''
  if (!title) return null
  const tpl: RoutineTemplate = {
    id: `mine-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    title,
    goal: body.instructions,
    trigger: body.trigger as RoutineTrigger,
    spec: body.spec,
  }
  if (body.note.trim()) tpl.note = body.note
  const next = [tpl, ...readTemplates(projectId)]
  if (!storageSetJson(key(projectId), next, 'local', MAX_CHARS)) return null
  return tpl
}

/** 删一条。 */
export function removeTemplate(projectId: string, id: string): RoutineTemplate[] {
  const next = readTemplates(projectId).filter((tpl) => tpl.id !== id)
  storageSetJson(key(projectId), next, 'local', MAX_CHARS)
  return next
}
