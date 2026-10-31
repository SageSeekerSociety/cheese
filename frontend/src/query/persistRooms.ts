// 最近去过的房间存在浏览器里（IndexedDB）：刷新、关掉再开，打开项目时频道清单、成员、
// 最近去过的房间最新一页当场就有，不等后端。放回来的只是第一帧，页面照旧去问一次
// （放回来的那份时间戳是存的时候的，早过了 30 秒新鲜期），回来的再替换上去。
//
// 存什么：每个项目的频道清单、成员、AI 队友；最近 MAX_ROOMS 个读过最新一页的房间的
// 那一页、这一行、成员。按项目分包存：一个房间归哪个项目，看它这一行或项目清单里有没有它，
// 认不出的不存。
//
// 谁能看到（判据）：
// - 存的人是谁写在包上，换了人不放回、整份清掉；退出登录清掉（`forgetRoomCache`）。
// - 放回哪个项目，以这一次启动刚问到的项目清单为准（index.html 抢先问的那一次，
//   `peekEarlyProjects`）：被移出的项目，它的包不放回；问不到这份清单就一个都不放回。
// - 放回来之后房间读最新一页被拒（401/403/404），对话栏换成空的（`topicWindow`）。
import type { DehydratedState, Query } from '@tanstack/vue-query'
import type { BlockWindow } from '@/query/blocks'

import { dehydrate, hydrate } from '@tanstack/vue-query'
import { createStore, del, get, set } from 'idb-keyval'

import { authToken } from '@/api/http'
import { PAGE_SIZE } from '@/lib/blockPaging'
import { peekEarlyProjects } from '@/lib/earlyProjects'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'

const KEY = 'rooms.v1'
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
export const MAX_ROOMS = 20
const SAVE_DELAY_MS = 3000

/** 项目这一层存的几份：`['project', 项目, 这一种]`。 */
const PROJECT_PARTS = new Set(['topics', 'members', 'agents'])
/** 房间这一层存的几份：`['room', 房间, ...]`，最新一页是 `blocks, newest`。 */
const ROOM_PARTS = new Set(['row', 'members', 'blocks'])

export interface SavedRooms {
  handle: string
  savedAt: number
  /** 项目 id → 这个项目的那几份。 */
  projects: Record<string, DehydratedState>
}

let store: ReturnType<typeof createStore> | null = null
function db(): ReturnType<typeof createStore> | null {
  if (typeof indexedDB === 'undefined') return null
  store ??= createStore('cheesex', 'queries')
  return store
}

function isNewestBlocks(key: readonly unknown[]): boolean {
  return key[0] === 'room' && key[2] === 'blocks' && key[3] === 'newest' && key.length === 4
}

function projectPart(key: readonly unknown[]): string | null {
  return key[0] === 'project' && key.length === 3 && typeof key[1] === 'string' && PROJECT_PARTS.has(key[2] as string)
    ? key[1]
    : null
}

function roomPart(key: readonly unknown[]): string | null {
  if (key[0] !== 'room' || typeof key[1] !== 'string') return null
  if (isNewestBlocks(key)) return key[1]
  return key.length === 3 && ROOM_PARTS.has(key[2] as string) && key[2] !== 'blocks' ? key[1] : null
}

/** 房间 → 项目：先看这个房间自己那一行，再看各项目的频道清单里有没有它。 */
function roomProjects(queries: Query[]): Map<string, string> {
  const owner = new Map<string, string>()
  for (const query of queries) {
    const pid = projectPart(query.queryKey)
    if (!pid || query.queryKey[2] !== 'topics') continue
    const rows = (query.state.data as { data?: { id?: unknown }[] } | undefined)?.data
    for (const row of rows ?? []) if (typeof row.id === 'string') owner.set(row.id, pid)
  }
  for (const query of queries) {
    const key = query.queryKey
    if (key[0] !== 'room' || key[2] !== 'row' || key.length !== 3) continue
    const pid = (query.state.data as { project_id?: unknown } | undefined)?.project_id
    if (typeof key[1] === 'string' && typeof pid === 'string') owner.set(key[1], pid)
  }
  return owner
}

/** 只留最新一页：往上翻出来的那些不存，窗口上面就还有更早的。 */
function trimWindow(window: BlockWindow): BlockWindow {
  if (window.blocks.length <= PAGE_SIZE) return window
  return { blocks: window.blocks.slice(-PAGE_SIZE), hasMore: true }
}

/** 此刻缓存里该存的那几份，按项目分好。 */
export function collectRooms(): SavedRooms['projects'] {
  const queries = queryClient
    .getQueryCache()
    .getAll()
    .filter((q) => q.state.status === 'success')
  const owner = roomProjects(queries)
  const recent = queries
    .filter((q) => isNewestBlocks(q.queryKey) && owner.has(q.queryKey[1] as string))
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)
    .slice(0, MAX_ROOMS)
    .map((q) => q.queryKey[1] as string)
  const keep = new Set(recent)
  const projectOf = new Map<string, string>()
  for (const query of queries) {
    const pid = projectPart(query.queryKey)
    if (pid) projectOf.set(query.queryHash, pid)
    const rid = roomPart(query.queryKey)
    if (rid && keep.has(rid)) projectOf.set(query.queryHash, owner.get(rid)!)
  }
  const state = dehydrate(queryClient, { shouldDehydrateQuery: (q) => projectOf.has(q.queryHash) })
  const projects: SavedRooms['projects'] = {}
  for (const query of state.queries) {
    const pid = projectOf.get(query.queryHash)!
    const data = isNewestBlocks(query.queryKey) ? trimWindow(query.state.data as BlockWindow) : query.state.data
    const bundle = (projects[pid] ??= { mutations: [], queries: [] })
    bundle.queries.push({ ...query, state: { ...query.state, data } })
  }
  return projects
}

async function save(): Promise<void> {
  const target = db()
  const handle = myHandle()
  if (!target || !handle || !authToken()) return
  const saved: SavedRooms = { handle, savedAt: Date.now(), projects: collectRooms() }
  // 存成字符串：缓存里有些数据是响应式代理，IndexedDB 的结构化克隆会拒绝它们。
  await set(KEY, JSON.stringify(saved), target)
}

/** 把存着的那份里、这次还在项目清单上的项目放回缓存。 */
export function restoreRooms(saved: SavedRooms, handle: string, allowed: Set<string>, now = Date.now()): number {
  if (saved.handle !== handle || now - saved.savedAt > MAX_AGE_MS) return 0
  let restored = 0
  for (const [pid, bundle] of Object.entries(saved.projects)) {
    if (!allowed.has(pid)) continue
    // 缓存里已有更新的那份（这期间已经问回来了）不会被旧的盖掉：hydrate 只放更新的。
    hydrate(queryClient, bundle)
    restored += bundle.queries.length
  }
  return restored
}

async function restore(): Promise<void> {
  const target = db()
  const handle = myHandle()
  if (!target || !handle || !authToken()) return
  const [raw, early] = await Promise.all([get<string>(KEY, target), peekEarlyProjects()])
  if (!raw) return
  let saved: SavedRooms
  try {
    saved = JSON.parse(raw) as SavedRooms
  } catch {
    await del(KEY, target)
    return
  }
  if (saved.handle !== handle) {
    await del(KEY, target)
    return
  }
  // 没问到这一次的项目清单就不知道现在还能看哪些项目：一个都不放。
  if (!early || myHandle() !== handle) return
  restoreRooms(saved, handle, new Set(early.data.map((project) => project.id)))
}

let timer: ReturnType<typeof setTimeout> | null = null
function scheduleSave(): void {
  if (timer) return
  timer = setTimeout(() => {
    timer = null
    void save().catch(() => {})
  }, SAVE_DELAY_MS)
}

function flush(): void {
  if (!timer) return
  clearTimeout(timer)
  timer = null
  void save().catch(() => {})
}

/** 打开页面时调一次：放回上次存的，之后这几份一变就（攒几秒）存一次。 */
export function persistRooms(): void {
  if (!db()) return
  void restore()
    .catch(() => {})
    .finally(() => {
      queryClient.getQueryCache().subscribe((event) => {
        if (event.type !== 'updated' || event.action.type !== 'success') return
        const key = event.query.queryKey
        if (projectPart(key) || roomPart(key)) scheduleSave()
      })
    })
  // 切走、关掉标签页时把攒着的那一次立刻存掉。
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}

/** 退出登录、换了人：存着的整份扔掉，攒着的那一次也不存了。 */
export function forgetRoomCache(): void {
  if (timer) clearTimeout(timer)
  timer = null
  const target = db()
  if (target) void del(KEY, target).catch(() => {})
}
