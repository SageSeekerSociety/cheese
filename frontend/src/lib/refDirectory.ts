// `<#id>` 指着什么：一个频道、一个任务、一条支线还是一份文档，叫什么，在哪个频道里。整个页面
// 共用一份。chip 画的时候来查（lib/refChip），没见过的 id 攒成一批问后端
// （`/addresses/refs`）；答案回来，读过它的 computed 会重画，文档和 Markdown 这种
// 自己画 DOM 的地方看 `refsVersion`。
//
// 后端说不认识的 id 记成 `missing`：chip 照实画成「引用不存在」，不猜一个名字。
// 一次没问成（断网、超时）不算不认识：下次画到它时再问。
//
// 问后端的那一步由 main.ts 接上（`answerRefsWith`）。这一层不碰网络：画 chip 的
// 组件到处都是，import 了网络层它们就都算成了自己取数据的组件。
import type { RouteLocationRaw } from 'vue-router'
import type { ConversationRef } from '../types/conversationRef'

import { reactive, ref } from 'vue'

export type RefLookup = ConversationRef | 'missing' | undefined

/** 一次最多问这么多个；后端那边也只答这么多（`REFS_PER_READ`）。 */
const PER_READ = 100

const known = reactive(new Map<string, ConversationRef | 'missing'>())
const asking = new Set<string>()
let batch: string[] = []
let answer: ((ids: string[]) => Promise<Record<string, ConversationRef>>) | null = null

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** 谁来答「这些 id 指着什么」：页面启动时接上后端（`resolveRefs`）。 */
export function answerRefsWith(resolve: (ids: string[]) => Promise<Record<string, ConversationRef>>): void {
  answer = resolve
}

/** 已经知道一个 id 指着什么（比如「被引用」列表带回来的）：记下，点它时 `refRoute`
 *  认得出去处，不用再问一次。 */
export function rememberRef(id: string, ref: ConversationRef): void {
  known.set(id, ref)
}

/** 每回来一批答案加一。自己画 DOM、没有 computed 可追的地方靠它重画。 */
export const refsVersion = ref(0)

/** 这个 id 指着什么；还不知道时是 undefined，同时去问。 */
export function lookupRef(id: string): RefLookup {
  const found = known.get(id)
  if (found !== undefined) return found
  if (!UUID.test(id)) return 'missing'
  if (!asking.has(id)) {
    asking.add(id)
    batch.push(id)
    if (batch.length === 1) queueMicrotask(flush)
  }
  return undefined
}

async function flush(): Promise<void> {
  const ids = batch
  batch = []
  const ask = answer
  if (!ask) return
  for (let at = 0; at < ids.length; at += PER_READ) {
    const chunk = ids.slice(at, at + PER_READ)
    try {
      const found = await ask(chunk)
      for (const id of chunk) known.set(id, found[id] ?? 'missing')
    } catch {
      // 没问成：留给下一次画到它的时候。
    } finally {
      for (const id of chunk) asking.delete(id)
    }
  }
  refsVersion.value += 1
}

/** 点一个 `<#id>` 去哪：支线开在它的频道里，任务开任务页，文档整页打开，其余当频道开。
 *  给了 `blockId` 就定位到那条消息（对话页认地址里的 `block`）。 */
export function refRoute(projectId: string, id: string, blockId?: string): RouteLocationRaw {
  const to = topicRoute(projectId, id)
  return blockId ? { ...to, query: { block: blockId } } : to
}

function topicRoute(projectId: string, id: string): RouteLocationRaw & object {
  const found = known.get(id)
  if (found && found !== 'missing') {
    if (found.kind === 'thread')
      return {
        name: 'workspace-thread',
        params: { projectId: found.project_id, topicId: found.room_id, threadId: id },
      }
    if (found.kind === 'task') return { name: 'workspace-task', params: { projectId: found.project_id, taskId: id } }
    if (found.kind === 'document')
      return { name: 'project-document', params: { projectId: found.project_id, docId: id } }
  }
  return { name: 'workspace-topic', params: { projectId, topicId: id } }
}
