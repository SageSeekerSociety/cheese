// 「AI 队友」页面上那两个数字 —— 记忆条数和「几个话题在用」—— 的算法。
//
// 它们不是随页面一起长出来的展示逻辑，是这一页存在的理由：光看名字和类型，
// 分不出哪个队友真的在干活、哪个是建完就没人用的空壳。所以这里单独成文件，
// 由测试直接盯着，而不是埋在组件里靠渲染结果间接验证。
import type { AgentFieldChoice, MemoryEntryOut } from '../api'
import type { AgentType, ProjectAgent, Topic } from '../cx_types'

// 一个队友在列表里的稳定键。项目从没配过队友时那条隐式的「芝士」没有 id
// （configured: false），拿 id 当 key 会让它和后来真建出来的第一个队友撞在一起，
// 所以退回 handle —— handle 在一个项目里本来就是唯一的。
export function agentKey(agent: Pick<ProjectAgent, 'id' | 'handle'>): string {
  return agent.id ?? `handle:${agent.handle}`
}

// 每个队友攒下了多少条记忆，按 handle 归。
//
// 记忆存的是一个扁平的 `{项目}:{handle}` 字符串，所以只能把 handle 切回来认领；
// 认不出来的（项目共享池、个人池）不属于任何一个队友，不计入任何一行。
export function memoryCountsByHandle(entries: MemoryEntryOut[], projectId: string): Record<string, number> {
  const counts: Record<string, number> = {}
  const prefix = `${projectId}:`
  for (const e of entries) {
    if (e.scope !== 'agent_project') continue
    if (!e.scope_id.startsWith(prefix)) continue
    const handle = e.scope_id.slice(prefix.length)
    if (!handle) continue
    counts[handle] = (counts[handle] ?? 0) + 1
  }
  return counts
}

// 每个队友当前被几个话题在用，按 agentKey 归。
//
// 两条判断决定了这个数字诚不诚实：
//   1. 话题没自己选队友（agent_instance_id 为 null）算在**默认队友**头上 ——
//      它不是「没人管」，是跟着项目默认走，换了默认它就跟着换。
//   2. 已归档的话题不算 —— 问的是「现在」谁在用，不是历史上谁用过。
export function topicCountsByAgent(topics: Topic[], agents: ProjectAgent[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const a of agents) counts[agentKey(a)] = 0
  const byId = new Map(agents.filter((a) => a.id).map((a) => [a.id as string, a]))
  const fallback = agents.find((a) => a.is_default)
  for (const t of topics) {
    if (t.archived_at) continue
    const owner = t.agent_instance_id ? byId.get(t.agent_instance_id) : fallback
    if (!owner) continue
    counts[agentKey(owner)] += 1
  }
  return counts
}

// 一个队友用的是哪个类型。类型可能已经被删掉，或者目录本身没读到 —— 那时
// 只有名字可用，不该因此让整行显示成「通用」，那是另一件事。
export function findType(types: AgentType[], name: string | null | undefined): AgentType | null {
  if (!name) return null
  return types.find((t) => t.name === name) ?? null
}

export function typeLabel(types: AgentType[], name: string | null | undefined): string {
  if (!name) return '通用'
  return findType(types, name)?.title || name
}

// handle 是记忆池的键，也会出现在 URL 里，所以取值受限：小写字母/数字开头，
// 之后可跟 `.` `_` `-`，最长 64。和后端 `_HANDLE_RE` 同一套规则 —— 前端先拦一道，
// 是为了让人当场看见哪里不对，而不是提交后收一个 422。
const HANDLE_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/

export function handleError(handle: string): string | null {
  if (!handle) return null // 留空 = 由后端按名字/类型生成
  return HANDLE_RE.test(handle) ? null : '只能用小写字母、数字和 . _ -，且以字母或数字开头'
}

export function displayNameError(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return '请填写名字'
  if (trimmed.length > 64) return '名字最长 64 个字'
  return null
}

// ---- 编辑器能提供什么设置 ----
//
// 后端那份目录（GET /agent-types/options）说了每个字段能不能设、不能设的理由。
// 把「怎么读它」放在这里而不是组件里，理由和这个文件开头那条一样：这是这一页
// 的承诺所在 —— 提供出来的每个值都必须真的会生效 —— 值得被测试直接盯着，而不是
// 靠在 jsdom 里点开一个浮层去间接验证。

export interface FieldOptionsLike {
  state: string
  choices: AgentFieldChoice[]
  reason: string
  note: string
}

export function fieldIsChoosable(options: Record<string, FieldOptionsLike>, name: string): boolean {
  return options[name]?.state === 'choosable'
}

export function fieldChoices(options: Record<string, FieldOptionsLike>, name: string): FieldOptionsLike['choices'] {
  // 只有 choosable 的字段才交出选项。一个 unavailable 的字段哪天带着残留的
  // choices 回来，也不该被渲染成能选 —— state 是唯一的判据。
  return fieldIsChoosable(options, name) ? options[name]?.choices ?? [] : []
}
