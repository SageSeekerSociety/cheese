// 项目框里的地址是给人看的：`/projects/<短名>/tasks/318`、`/docs/42`、`/channels/7`。
// 页面和接口认的仍是 UUID，所以这里做两件事：
//
// - 进项目框之前（`canonicalAddress`，一个全局守卫）把地址改写成短的那一种：参数里
//   是 UUID 或旧短名的，换成现在的短名和编号再落地。代码里照旧拿 UUID 拼路由，发出
//   去的旧链接也一样，落地时都会变成短地址。
// - 页面拿到的 props（`addressProps`）换回 UUID：守卫已经把这一次要用的对照表读好，
//   这里同步查表。直接读 `route.params` 的地方用 `routeIds`。
//
// 还没编号的（私聊、任务自己的文档）地址里照旧是 UUID。
import type { RouteLocationNormalized, RouteLocationRaw, RouteParamsGeneric } from 'vue-router'
import type { NumberedKind, ThingAddress } from '../api/addresses'

import { addressOf, resolveNumber, resolveProject } from '../api/addresses'
import { ApiError } from '../api/http'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NUMBER = /^\d+$/

export function isUuid(value: string): boolean {
  return UUID.test(value)
}

/** 路由参数名 → 它装的是哪一种编号。 */
const PARAM_KINDS: [param: string, kind: NumberedKind][] = [
  ['topicId', 'channels'],
  ['taskId', 'tasks'],
  ['docId', 'docs'],
]

// 项目：短名（含旧短名）→ UUID，UUID → 现在的短名。
const projectIdByRef = new Map<string, string>()
const slugById = new Map<string, string>()
// 编号的东西：`项目/种类/编号` 和 `种类/UUID` 都指向同一份地址。
const byNumber = new Map<string, ThingAddress>()
const byId = new Map<string, ThingAddress>()
// 换不出 UUID 的项目参数，和服务端给的原因（`projectRefusal`）。
const refusedByRef = new Map<string, ProjectRefusal>()
// 同一个查询只发一次。
const pending = new Map<string, Promise<unknown>>()

function once<T>(key: string, load: () => Promise<T>): Promise<T> {
  const running = pending.get(key) as Promise<T> | undefined
  if (running) return running
  const started = load().finally(() => pending.delete(key))
  pending.set(key, started)
  return started
}

export function rememberProject(id: string, slug: string): void {
  const before = slugById.get(id)
  if (before && before !== slug) projectIdByRef.set(before, id)
  slugById.set(id, slug)
  projectIdByRef.set(slug, id)
  projectIdByRef.set(id, id)
}

export function rememberThing(address: ThingAddress): void {
  rememberProject(address.project_id, address.slug)
  byId.set(`${address.kind}/${address.id}`, address)
  if (address.number != null) byNumber.set(`${address.project_id}/${address.kind}/${address.number}`, address)
}

/** 接口回来的项目带着短名（`slug`），先记下。 */
export function rememberProjects(rows: { id: string; slug?: string }[]): void {
  for (const row of rows) if (row.slug) rememberProject(row.id, row.slug)
}

/** 列表里已经带着编号的东西（任务、频道、文档），先记下，点进去就不用再问一次。 */
export function rememberNumbered(
  kind: NumberedKind,
  rows: { id: string; project_id: string; number?: number | null; room_id?: string | null }[]
): void {
  for (const row of rows) {
    const slug = slugById.get(row.project_id)
    if (!slug || row.number == null) continue
    rememberThing({
      project_id: row.project_id,
      slug,
      kind,
      id: row.id,
      room_id: kind === 'channels' ? row.id : row.room_id ?? null,
      number: row.number,
    })
  }
}

/** 地址里的项目参数（短名、旧短名或 UUID）对应的 UUID；还没查过的原样返回。 */
export function projectUuid(ref: string): string {
  return projectIdByRef.get(ref) ?? ref
}

/**
 * 地址里的项目为什么换不出 UUID：没登录（`unauthenticated`），或者服务端说没有这个项目
 * （`missing`——不是成员也答成没有，后端不肯透露一个看不到的项目存不存在）。查得到的、
 * 还没查过的、或者只是网络没通的，都没有原因。
 *
 * 页面拿着换不出的短名去请求，只会被当成参数不合法挡回来：得在请求之前就知道进不来。
 */
export type ProjectRefusal = 'unauthenticated' | 'missing'

export function projectRefusal(ref: string): ProjectRefusal | undefined {
  return refusedByRef.get(ref)
}

export function projectSlug(id: string): string | undefined {
  return slugById.get(id)
}

/** 地址里一个编号参数对应的 UUID；本来就是 UUID 或还没查过的原样返回。 */
export function thingUuid(projectRef: string, kind: NumberedKind, value: string): string {
  if (!NUMBER.test(value)) return value
  return byNumber.get(`${projectUuid(projectRef)}/${kind}/${value}`)?.id ?? value
}

function taskRoom(projectRef: string, taskValue: string): string | undefined {
  const id = thingUuid(projectRef, 'tasks', taskValue)
  return byId.get(`tasks/${id}`)?.room_id ?? undefined
}

/** 这条路由参数里的 UUID：项目、频道、任务、文档。任务页还要它所在的频道。 */
export function routeIds(params: RouteParamsGeneric): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, raw] of Object.entries(params)) {
    if (typeof raw === 'string') out[key] = raw
  }
  const project = out.projectId
  if (project === undefined) return out
  out.projectId = projectUuid(project)
  for (const [param, kind] of PARAM_KINDS) {
    if (out[param] !== undefined) out[param] = thingUuid(project, kind, out[param])
  }
  if (out.taskId !== undefined && out.topicId === undefined) {
    const room = taskRoom(project, params.taskId as string)
    if (room) out.topicId = room
  }
  return out
}

/** 项目框里各页的 props：地址参数换成 UUID。 */
export function addressProps(route: RouteLocationNormalized): Record<string, string> {
  return routeIds(route.params)
}

/**
 * 一个拿 UUID 拼的站内地址，换成已经知道的短名和编号：复制出去的链接是短的。不认识
 * 的原样留着，点开时守卫照样会换。
 */
export function shortRoute(to: RouteLocationRaw): RouteLocationRaw {
  if (typeof to === 'string' || !('params' in to) || !to.params) return to
  const params: Record<string, string> = {}
  for (const [key, raw] of Object.entries(to.params)) if (typeof raw === 'string') params[key] = raw
  const projectId = params.projectId
  if (projectId === undefined) return to
  const project = projectUuid(projectId)
  const slug = slugById.get(project)
  if (slug) params.projectId = slug
  for (const [param, kind] of PARAM_KINDS) {
    const value = params[param]
    if (value === undefined || !isUuid(value)) continue
    const found = byId.get(`${kind}/${value}`)
    if (found?.number != null && found.project_id === project) params[param] = String(found.number)
  }
  if ('name' in to && to.name === 'workspace-task') delete params.topicId
  return { ...to, params } as RouteLocationRaw
}

async function projectOf(ref: string): Promise<{ id: string; slug: string } | null> {
  const known = projectIdByRef.get(ref)
  const slug = known ? slugById.get(known) : undefined
  // 旧短名查得到 UUID，但地址要换成现在的短名，所以只认现在的短名和 UUID 本身。
  // 侧栏点频道时给的是 UUID：认得它，切一次频道就少等一个来回。
  if (known && slug && (slug === ref || known === ref)) return { id: known, slug }
  try {
    const found = await once(`p/${ref}`, () => resolveProject(ref))
    rememberProject(found.id, found.slug)
    if (!isUuid(ref)) projectIdByRef.set(ref, found.id)
    refusedByRef.delete(ref)
    return found
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) refusedByRef.set(ref, 'unauthenticated')
    else if (e instanceof ApiError && (e.status === 403 || e.status === 404)) refusedByRef.set(ref, 'missing')
    return null
  }
}

async function thingById(kind: NumberedKind, id: string): Promise<ThingAddress | null> {
  const known = byId.get(`${kind}/${id}`)
  if (known) return known
  try {
    const found = await once(`of/${kind}/${id}`, () => addressOf(kind, id))
    rememberThing(found)
    return found
  } catch {
    return null
  }
}

async function thingByNumber(projectId: string, kind: NumberedKind, number: string): Promise<ThingAddress | null> {
  const known = byNumber.get(`${projectId}/${kind}/${number}`)
  if (known) return known
  try {
    const found = await once(`n/${projectId}/${kind}/${number}`, () => resolveNumber(projectId, kind, number))
    rememberThing(found)
    return found
  } catch {
    return null
  }
}

/**
 * 没登录的人进项目框：不查，短名一个也换不出来（每个项目都要登录才查得到），直接记成
 * 「要登录」。UUID 不用记，页面拿它去请求，服务端自己会答要登录。
 */
export function noteSignedOut(to: RouteLocationNormalized): true {
  if (!to.matched.some((r) => r.meta?.projectFrame === true)) return true
  const ref = to.params.projectId
  if (typeof ref === 'string' && ref && !isUuid(ref)) refusedByRef.set(ref, 'unauthenticated')
  return true
}

/**
 * 全局守卫：项目框里的地址落地成短的那一种。查不到的（没有这个项目、看不到、编号不
 * 存在）原样放行，由页面自己说找不到；项目查不到的原因记在 `projectRefusal`。
 */
export async function canonicalAddress(to: RouteLocationNormalized): Promise<true | RouteLocationRaw> {
  if (!to.matched.some((r) => r.meta?.projectFrame === true)) return true
  const ref = to.params.projectId
  if (typeof ref !== 'string' || !ref) return true
  const project = await projectOf(ref)
  if (!project) return true

  // 资料库里打开文档的旧地址 `library?doc=<UUID>`。
  const doc = to.query.doc
  if (to.name === 'project-library' && typeof doc === 'string') {
    const query = { ...to.query }
    delete query.doc
    const found = await thingById('docs', doc)
    return {
      name: 'project-document',
      params: { projectId: project.slug, docId: found?.number != null ? String(found.number) : doc },
      query,
      hash: to.hash,
    }
  }

  const params: Record<string, string> = {}
  for (const [key, raw] of Object.entries(to.params)) if (typeof raw === 'string') params[key] = raw
  let changed = params.projectId !== project.slug
  params.projectId = project.slug
  for (const [param, kind] of PARAM_KINDS) {
    const value = params[param]
    if (value === undefined) continue
    if (isUuid(value)) {
      const found = await thingById(kind, value)
      if (found?.number != null && found.project_id === project.id) {
        params[param] = String(found.number)
        changed = true
      }
    } else if (NUMBER.test(value)) {
      await thingByNumber(project.id, kind, value)
    }
  }
  if (!changed) return true
  // 不写 `replace`：改写沿用原来那一跳，点链接仍在身后留一格，返回键才回得去。
  return { name: to.name ?? undefined, params, query: to.query, hash: to.hash } as RouteLocationRaw
}
