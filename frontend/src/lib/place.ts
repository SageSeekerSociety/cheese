// 一个「地点」= 一个房间，或房间里的一条支线。
//
// 工作区从头到尾用**一个 id** 定位一个地点：`/blocks`、`/doc`、`/transcript`、
// `/usage` 全都拿这一个 id 作答，支线的 id 一样认。所以下面那些面板一直管它叫
// `topic` 的东西，其实是「地点」——房间是一个，支线也是一个。
//
// 于是打开一条支线，缺的不是一套新组件，是**让这个 id 在前端也变成一个地点对象**。
// `store.topics` 里永远没有支线（它来自 `GET /topics?project_id=`，只查 topics
// 表，而侧栏只列房间是设计，不是 bug），所以那张表答不了这个问题，得直接去问
// `GET /topics/{id}`——那条接口早就会对支线返回一行 task。
import type { RoomTask, Topic } from '@/cx_types'

/** `GET /topics/{id}` 的两种回答：房间是 Topic，支线是 RoomTask。 */
export type PlacePayload = Topic | RoomTask

/** 是支线还是房间。按 `room_id` 认——只有支线有「我挂在哪个房间」这个问题。 */
export function isThreadPayload(place: PlacePayload): place is RoomTask {
  return typeof (place as RoomTask).room_id === 'string'
}

/**
 * 把一条支线变成下面那些面板要的地点对象。
 *
 * 只有 `kind` 是合成的，其余每个字段都是这条支线自己的值：
 *
 * - `id` 用支线的，因为按地点寻址的每一条接口要的就是它；
 * - `parent_id` 填它所在的房间——支线不嵌套，上面永远是房间，头部据此告诉人
 *   「你在哪」；
 * - `kind` 是 `'thread'`，一个真的第四种值，不是借 `'topic'` 用一下：支线正是
 *   那个**不再是房间**的东西，读 kind 的地方（名册、算力、侧栏）必须分得出来。
 *
 * 支线没有的字段就不给（`archived_at`、未读、`running`…），而不是编一个：
 * 支线不归档，也没有自己的未读游标。
 */
export function threadAsPlace(thread: RoomTask): Topic {
  return {
    id: thread.id,
    project_id: thread.project_id,
    parent_id: thread.room_id,
    title: thread.title,
    kind: 'thread',
    status: thread.status,
    created_at: thread.created_at,
    updated_at: thread.updated_at,
    accepted_by: thread.accepted_by ?? null,
    accepted_at: thread.accepted_at ?? null,
    upgraded_from_block_id: thread.upgraded_from_block_id ?? null,
    agent_instance_id: thread.agent_instance_id ?? null,
  }
}

/** 任何一个地点归一成 Topic 形状。房间原样返回。 */
export function asPlace(place: PlacePayload): Topic {
  return isThreadPayload(place) ? threadAsPlace(place) : place
}

export function isThread(place: Topic): boolean {
  return place.kind === 'thread'
}

/**
 * 这个地点的**房间**是哪个。
 *
 * 名册、未读、算力、这个房间派出去的活——这几样只有房间答得了，支线问它们要么
 * 404，要么答的是别人的。支线的房间在 `parent_id` 上（见 `threadAsPlace`），
 * 房间的房间就是它自己。
 */
export function roomIdOf(place: Topic): string {
  return isThread(place) ? (place.parent_id ?? place.id) : place.id
}
