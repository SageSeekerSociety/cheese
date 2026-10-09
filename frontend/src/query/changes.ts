// 房间推来的「某样东西变了」（`state` 帧）在缓存里意味着什么：把哪几份标过期。看着
// 它们的地方自己重读，没人看的下次打开时再读 —— 不用每个页面各接一遍。
//
// 帧只说「变了」，不带新的样子（第 6 步之前），所以这里只让它们再读一次、不改数据：正在
// 路上的那次读回来之后再读一次，一串连着来的帧也只多读一次（`refreshQueries`）。
import { refreshQueries } from '@/query/client'
import { keys } from '@/query/keys'
import { refreshTopicRow } from '@/query/project'

export interface RoomChange {
  /** 推来这一帧的房间（频道；任务和支线的帧也说的是它们所在的频道）。 */
  room: string
  project: string | null
  /** 帧的 `resource`。 */
  resource: string
  /** 后端指名的那一行（`topics` 时是房间 id）；没指名就是整块。 */
  id?: string
}

function invalidate(queryKey: readonly unknown[]): Promise<void> {
  return refreshQueries({ queryKey })
}

/** 任务变了（建、改名、开始、交付、关闭、换人）：房间和项目的任务清单、每一件任务本身。 */
function tasksChanged(room: string, project: string | null): Promise<unknown> {
  return Promise.all([
    invalidate(keys.roomTasks(room)),
    project ? invalidate(keys.projectTasks(project)) : null,
    refreshQueries({ predicate: (query) => query.queryKey[0] === 'room' && query.queryKey[2] === 'task' }),
  ])
}

/** 这一帧说的东西在缓存里有没有一份；没有的（采纳卡、提案卡、文档）由页面自己接。 */
export function roomChanged({ room, project, resource, id }: RoomChange): Promise<unknown> | null {
  switch (resource) {
    // 「topics」说房间这一行变了，也说任务清单变了（建、改名、关）。指名了那一行就只
    // 重取它 —— 改一个房间名不再重下整份清单（400 多个话题近 300KB）。
    case 'topics':
      return Promise.all([
        project ? (id ? refreshTopicRow(project, id) : invalidate(keys.projectTopics(project))) : null,
        tasksChanged(room, project),
      ])
    case 'tasks':
      return tasksChanged(room, project)
    case 'pins':
      return invalidate(keys.roomPins(room))
    case 'threads':
      return invalidate(keys.roomThreads(room))
    default:
      return null
  }
}
