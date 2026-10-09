// 项目框架（侧栏的频道清单、每个房间的未读、提醒档位）在别人做了点什么时会变：别的频道
// 有人说话、改了名、建了任务、芝士开始或停下，我在另一个标签页读过了。项目页在页面那一
// 条房间连接上订阅这个项目（`project:<id>`，后端 `project_feed.py`），哪一样在哪个房间
// 变了就推一帧过来，这里把那一份重读一次。
//
// 帧只说「哪样变了、在哪」，不带新的样子：清单那一行带着「我的任务」，未读是每个人各算
// 各的，同一帧发给项目里所有人，带不了。只有自己能看见的房间（私聊、私有频道）只告诉
// 里面的人。
//
// 订上的那一刻（第一次和每次重连）四样各重读一次：订上之前的变化不会再推过来。连接
// 断了按退避重新订阅；被拒（不是这个项目的人、凭据失效）就不再试。
import type { Ref } from 'vue'

import { onScopeDispose, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { openRoomChannel, type RoomChannel } from '@/lib/roomLink'
import { refreshQueries } from '@/query/client'
import { keys } from '@/query/keys'
import { refreshTopicRow } from '@/query/project'

interface FeedFrame {
  type?: string
  resource?: string
  id?: string
  code?: string
}

const REFUSALS = new Set(['auth_required', 'auth_expired', 'forbidden'])
const HEARTBEAT_INTERVAL_MS = 15_000
const HEARTBEAT_TIMEOUT_MS = 10_000
const MAX_RETRY_MS = 30_000

/** 这一帧说的那一样在缓存里对应哪一份，让它重读。 */
export function projectChanged(projectId: string, me: string | null, frame: FeedFrame): Promise<unknown> | null {
  switch (frame.resource) {
    case 'topics':
      return frame.id
        ? refreshTopicRow(projectId, frame.id)
        : refreshQueries({ queryKey: keys.projectTopics(projectId) })
    case 'unread':
      return me ? refreshQueries({ queryKey: keys.projectUnread(projectId, me) }) : null
    case 'private_unread':
      return me ? refreshQueries({ queryKey: keys.projectPrivateUnread(projectId, me) }) : null
    case 'notify_levels':
      return refreshQueries({ queryKey: keys.projectNotifyLevels(projectId) })
    default:
      return null
  }
}

/** 订上了：订上之前的变化不会再推过来，四样各重读一次。 */
function readAll(projectId: string, me: string | null): void {
  void refreshQueries({ queryKey: keys.projectTopics(projectId) })
  void refreshQueries({ queryKey: keys.projectNotifyLevels(projectId) })
  if (me) {
    void refreshQueries({ queryKey: keys.projectUnread(projectId, me) })
    void refreshQueries({ queryKey: keys.projectPrivateUnread(projectId, me) })
  }
}

/** 看着这个项目：它的清单、未读、提醒档位一变就重读。 */
export function useProjectFeed(projectId: Ref<string>, me: string | null): void {
  let channel: RoomChannel | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryMs = 1000
  let refused = false
  let heartbeat: ReturnType<typeof setInterval> | null = null
  let pongTimer: ReturnType<typeof setTimeout> | null = null

  function stopHeartbeat() {
    if (heartbeat) clearInterval(heartbeat)
    if (pongTimer) clearTimeout(pongTimer)
    heartbeat = null
    pongTimer = null
  }

  function close() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    stopHeartbeat()
    const open = channel
    channel = null
    if (open) {
      open.onopen = open.onmessage = open.onclose = open.onerror = null
      open.close()
    }
  }

  function retry(id: string) {
    if (refused || retryTimer) return
    retryTimer = setTimeout(() => {
      retryTimer = null
      if (projectId.value === id) open(id)
    }, retryMs)
    retryMs = Math.min(retryMs * 2, MAX_RETRY_MS)
  }

  function open(id: string) {
    close()
    const ws = openRoomChannel(`project:${id}`)
    channel = ws
    ws.onopen = () => {
      retryMs = 1000
      readAll(id, me)
      // 一个房间都没开着的时候（总览、任务清单），这条连接死了只有它自己发现得了。
      heartbeat = setInterval(() => {
        if (channel !== ws || pongTimer) return
        ws.send(JSON.stringify({ type: 'ping' }))
        pongTimer = setTimeout(() => {
          pongTimer = null
          if (channel === ws) ws.drop()
        }, HEARTBEAT_TIMEOUT_MS)
      }, HEARTBEAT_INTERVAL_MS)
    }
    ws.onmessage = (event) => {
      if (channel !== ws) return
      let frame: FeedFrame
      try {
        frame = JSON.parse(event.data) as FeedFrame
      } catch {
        return
      }
      if (pongTimer) clearTimeout(pongTimer)
      pongTimer = null
      if (frame.type === 'error' && frame.code && REFUSALS.has(frame.code)) refused = true
      else if (frame.type === 'state') void projectChanged(id, me, frame)
    }
    ws.onclose = () => {
      if (channel !== ws) return
      channel = null
      stopHeartbeat()
      retry(id)
    }
  }

  watch(
    projectId,
    (id) => {
      refused = false
      retryMs = 1000
      if (id) open(id)
      else close()
    },
    { immediate: true }
  )
  // 断网回来不等退避：马上重新订阅，订上时那四样各读一次。
  useEventListener(window, 'online', () => {
    if (!refused && !channel && projectId.value) {
      retryMs = 1000
      open(projectId.value)
    }
  })
  onScopeDispose(close)
}
