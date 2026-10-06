// 命令面板里的内容搜索：消息、任务、文档、项目文档、资料库五个数据源，问的是同一个
// 后端接口。同一次输入只问一次，五个数据源各取自己那一份。
import type { Router } from 'vue-router'
import type { ProjectSearchHits } from '@/api/projectSearch'
import type { PaletteItem, PaletteSource } from '@/commands/palette/sources'

import { searchProject } from '@/api/projectSearch'
import { teammateName } from '@/lib/agentNames'
import { topicTitle } from '@/lib/topicState'

// 只为让五个数据源合用一次请求，不是缓存：过一会儿再搜同样的字要看到新内容。
const FRESH_MS = 10_000
const asked = new Map<string, { at: number; hits: Promise<ProjectSearchHits> }>()

export function hitsFor(projectId: string, query: string): Promise<ProjectSearchHits> {
  const key = `${projectId}\n${query}`
  const now = Date.now()
  for (const [k, entry] of asked) if (now - entry.at > FRESH_MS) asked.delete(k)
  let entry = asked.get(key)
  if (!entry) {
    entry = { at: now, hits: searchProject(projectId, query) }
    // 失败的那次不留：下次输入同样的字要重新问。
    entry.hits.catch(() => asked.delete(key))
    asked.set(key, entry)
  }
  return entry.hits
}

/** 结果在哪个房间：还没起名的房间按读者的语言叫「新话题」。 */
export function searchRoomTitle(hit: { room_title: string }): string {
  return topicTitle({ title: hit.room_title })
}

/** 结果是谁写的：`@` 加他现在的名字；没有名字时写他的 handle，不带 `@`。 */
export function searchAuthor(hit: ProjectSearchHits['records'][number]): string {
  const name = teammateName(hit.author_name, hit.author_name_source)
  return name ? `@${name}` : hit.author
}

/** 结果下面那一行：在哪个房间、谁、什么时候。 */
export function whereAndWhen(...parts: (string | null | undefined)[]): string {
  return parts.filter(Boolean).join(' · ')
}

/**
 * 内容的一类：面板里是一组结果，搜索结果页上是一栏。两处用同一份「搜到的东西长什么
 * 样、点开去哪」，所以写在一起。
 */
export interface ContentKind {
  id: string
  /** i18n key：面板里那一组、结果页上那一栏的名字。 */
  label: string
  /** 后端 `only` 认的名字：这一栏只搜这几类。 */
  only: string[]
  itemsOf: (hits: ProjectSearchHits, projectId: string, router: Router) => PaletteItem[]
}

/** 一类内容在面板里的数据源：`?` 只看内容。 */
export function contentSource(kind: ContentKind, order: number): PaletteSource {
  return {
    id: kind.id,
    label: kind.label,
    order,
    prefix: '?',
    async search(query, { projectId, router }) {
      if (!projectId) return []
      return kind.itemsOf(await hitsFor(projectId, query), projectId, router)
    },
  }
}
