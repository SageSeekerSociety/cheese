import type { ComputedRef, Ref } from 'vue'
import type { TopicNotifyLevel, TopicNotifySetting, TopicUnread } from '@/api'
import type { RoomTask, Topic } from '@/cx_types'
import type { ChannelSpeakers } from '@/types/channels'
import type { RememberedPlace } from './types'

import {
  archiveTopic,
  createTopic,
  deleteTopic,
  joinChannel,
  leaveChannel,
  markAllTopicsRead,
  markTopicRead,
  setChannelDescription,
  setTopicNotifyLevel,
  setTopicTitle,
  unarchiveTopic,
  upgradeBlock,
} from '@/api'
import { renameTask as renameTaskApi } from '@/api/tasks'
import { setChannelMembersOnly, setChannelSpeakers } from '@/api/topicMembers'
import { t } from '@/i18n'
import { patchQuery } from '@/query/client'
import { keys } from '@/query/keys'
import { patchTopics } from '@/query/project'

export interface ChannelDeps {
  pid: ComputedRef<string>
  projectId: Ref<string | null>
  me: ComputedRef<string>
  topics: ComputedRef<Topic[]>
  unreadMap: ComputedRef<Record<string, TopicUnread>>
  privateUnreadMap: ComputedRef<Record<string, number>>
  notifyLevels: ComputedRef<Record<string, TopicNotifySetting>>
  lastPlaceByProject: Ref<Record<string, RememberedPlace>>
  rememberPlace: (projectKey: string, place: RememberedPlace) => void
  forgetPlace: (projectKey: string) => void
  patchRows: (topicId: string, change: (topic: Topic) => Topic) => Promise<void>
  refreshTopics: () => Promise<void>
  refreshUnread: () => Promise<void>
  reportError: (e: unknown, fallback: string) => void
}

/**
 * 写频道和任务的那些动作：改通知档位、说明、可见性、加入退出、改名、归档、删除、
 * 新建、转任务，和「点开就算读过」的那几盏灯。
 *
 * 它们改的都不是 store 自己的状态，而是 query 缓存里那几份清单（先本地改、服务端回来
 * 再覆盖），所以这一层拿到的是那几份的 ref 和几个「本地改一行」的帮手。
 */
export function useChannelActions(deps: ChannelDeps) {
  const {
    pid,
    projectId,
    me,
    topics,
    unreadMap,
    privateUnreadMap,
    notifyLevels,
    lastPlaceByProject,
    rememberPlace,
    forgetPlace,
    patchRows,
    refreshTopics,
    refreshUnread,
    reportError,
  } = deps

  /** 改我对一个频道的通知档位（静音可以带截止时间）。先改本地，失败了改回去并说一声。 */
  async function setNotifyLevel(topicId: string, level: TopicNotifyLevel, until: string | null = null) {
    const key = keys.projectNotifyLevels(pid.value)
    // 只改、只还原这一个：别的频道这期间被轮询或另一次设置改过的，不跟着回滚。
    const before = notifyLevels.value[topicId]
    const put = (setting: TopicNotifySetting | undefined) =>
      patchQuery<Record<string, TopicNotifySetting>>(key, (levels) => {
        const next = { ...levels }
        if (setting && setting.level !== 'mentions') next[topicId] = setting
        else delete next[topicId]
        return next
      })
    await put({ level, muted_until: level === 'mute' ? until : null })
    try {
      await setTopicNotifyLevel(topicId, level, level === 'mute' ? until : null)
    } catch (e) {
      await put(before)
      reportError(e, t('work.room.menu.notifyFailed'))
    }
    // 数字跟着档位变，以服务器为准再拉一次。
    void refreshUnread()
  }

  /** 写频道说明（管理者）。成功之后这一行跟着变。 */
  async function describe(topicId: string, description: string): Promise<boolean> {
    try {
      const saved = await setChannelDescription(topicId, description)
      await patchRows(topicId, (topic) => ({ ...topic, description: saved.description ?? null }))
      return true
    } catch (e) {
      reportError(e, t('work.channel.describeFailed'))
      return false
    }
  }

  /** 谁能在主线发言、支线是否人人能回（管理者，#4795）。成功之后这一行跟着变。 */
  async function setSpeakers(topicId: string, speakerRoles: string[] | null, threadRepliesOpen: boolean) {
    try {
      const saved = await setChannelSpeakers(topicId, speakerRoles, threadRepliesOpen)
      const said = saved as Topic & ChannelSpeakers
      await patchRows(
        topicId,
        (topic) =>
          ({
            ...topic,
            speaker_roles: said.speaker_roles ?? null,
            thread_replies_open: said.thread_replies_open !== false,
          }) as Topic
      )
      return true
    } catch (e) {
      reportError(e, t('work.channel.speakersFailed'))
      return false
    }
  }

  /** 设为私密 / 重新公开（管理者；公开只有项目管理员）。成功之后这一行跟着变。 */
  async function setMembersOnly(topicId: string, membersOnly: boolean): Promise<boolean> {
    try {
      const saved = await setChannelMembersOnly(topicId, membersOnly)
      await patchRows(topicId, (topic) => ({ ...topic, members_only: saved.members_only === true }))
      void refreshTopics()
      return true
    } catch (e) {
      reportError(e, t('work.channel.visibilityFailed'))
      return false
    }
  }

  /** 加入 / 退出一个频道。成功之后这一行的 `joined` 跟着变，侧栏随之出现或消失。 */
  async function setJoined(topicId: string, joined: boolean): Promise<boolean> {
    try {
      await (joined ? joinChannel(topicId) : leaveChannel(topicId))
    } catch (e) {
      reportError(e, t(joined ? 'work.channel.joinFailed' : 'work.channel.leaveFailed'))
      return false
    }
    await patchRows(topicId, (topic) => ({ ...topic, joined }))
    void refreshTopics()
    void refreshUnread()
    return true
  }

  /** 全部标为已读：先清掉本地角标，再让服务器把每一间的已读位推到现在。 */
  async function markAllRead() {
    const project = pid.value
    if (!project) return
    await patchQuery<Record<string, TopicUnread>>(keys.projectUnread(project, me.value), () => ({}))
    // 每一间的已读位都推到了现在，所以跟着它走的两盏灯也一起灭（见 `clearReadLights`）。
    void patchTopics(project, (rows) => rows.map(clearReadLights))
    try {
      await markAllTopicsRead(project)
    } catch (e) {
      reportError(e, t('work.room.menu.markAllReadFailed'))
    }
    // 成功失败都以服务器为准再拉一次：失败了角标回来的是此刻真实的数，不是点之前那一份。
    void refreshUnread()
  }

  // Opening a topic = reading it: bump the server-side cursor and clear the
  // badge locally (optimistic — the next refresh agrees).
  //
  // `topicId` 是一段对话：频道自己的，或一个任务的（任务的未读只亮给负责人和协作者）。
  function markRead(topicId: string) {
    if (!me.value) return
    if (unreadMap.value[topicId] !== undefined) {
      void patchQuery<Record<string, TopicUnread>>(keys.projectUnread(pid.value, me.value), (map) => {
        const next = { ...map }
        delete next[topicId]
        return next
      })
    }
    forgetReadLights(topicId)
    markTopicRead(topicId, me.value).catch(() => {})
  }

  // 「点开过就灭」：点开的那一行（频道或一件活）交付完的绿灯当场灭掉，连同那一行
  // 「有人回复了我」的黄灯（两者都是跟着已读位走的，一次点开一起灭）。这正是上面那
  // 一次 `markTopicRead` 在服务端记下的同一笔——差别只是服务端要等下一次清单回来，
  // 而这两个灯是「你刚点开」当场就该变的。
  //
  // 两种点开都算，和后端算这一位时比的同一个东西（那儿把已读位和采纳时刻取
  // `GREATEST`）：开那个频道，或者开它名下的一件活。所以点开一件活时，灭掉的是挂着
  // 它的那个频道那一行——绿灯只画在频道行上（活一关就从侧栏消失，只剩它的频道）。
  // 黄灯跟进的是这间房自己的已读位：打开这个频道就算处理了，所以点开的正是这个频道
  // 时它当场灭。点开它名下的一件活推的是活自己那一位，动不了它（点开一条支线在服务
  // 端也算处理了，那一条要等下一次清单回来才灭）。
  function forgetReadLights(topicId: string) {
    const project = pid.value
    if (!project) return
    void patchTopics(project, (rows) =>
      rows.map((topic) => {
        if (topic.id === topicId) return clearReadLights(topic)
        const my = topic.my_tasks
        if (!my) return topic
        const at = my.shown.findIndex((task) => task.id === topicId && task.unseen_delivery)
        if (at < 0) return topic
        return {
          ...topic,
          my_tasks: {
            ...my,
            shown: my.shown.map((task, i) => (i === at ? { ...task, unseen_delivery: false } : task)),
          },
        }
      })
    )
  }

  /** 这一行上跟着已读位走的两盏灯灭掉（绿灯；「有人回复了我」的黄灯），含它名下那几
   *  件活的绿灯；本来就没亮的原样返回，省掉一次整行重画。 */
  function clearReadLights(topic: Topic): Topic {
    const my = topic.my_tasks
    if (!my) {
      if (!topic.unseen_delivery && !topic.replied_to_me) return topic
      return { ...topic, unseen_delivery: false, replied_to_me: false }
    }
    const shown = my.shown.map((task) => (task.unseen_delivery ? { ...task, unseen_delivery: false } : task))
    const lit = shown.some((task, i) => task !== my.shown[i])
    if (!topic.unseen_delivery && !topic.replied_to_me && !lit) return topic
    return {
      ...topic,
      unseen_delivery: false,
      replied_to_me: false,
      my_tasks: lit ? { ...my, shown } : my,
    }
  }

  // Same, for a 私聊 — its badge is keyed by peer handle, not topic id.
  function markDmRead(topicId: string, peerKey: string) {
    if (!me.value) return
    if (privateUnreadMap.value[peerKey] !== undefined) {
      void patchQuery<Record<string, number>>(keys.projectPrivateUnread(pid.value, me.value), (map) => {
        const next = { ...map }
        delete next[peerKey]
        return next
      })
    }
    markTopicRead(topicId, me.value).catch(() => {})
  }

  async function renameTopic(topicId: string, title: string) {
    try {
      const updated = await setTopicTitle(topicId, title)
      await patchRows(topicId, (topic) => ({ ...topic, title: updated.title }))
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.rename'))
    }
  }

  /** 给一件任务改名（侧栏任务行 ⋯ 里那一项）。任务行挂在频道的 `my_tasks` 里，
   *  侧栏读的就是这一份：改名成功后把那一行就地换成新名字，不等房间推来的帧。 */
  async function renameTask(taskId: string, roomId: string, title: string) {
    try {
      const updated = await renameTaskApi(taskId, title)
      const project = projectId.value
      if (!project) return
      await patchTopics(project, (rows) =>
        rows.map((topic) => {
          const my = topic.my_tasks
          if (topic.id !== roomId || !my) return topic
          const at = my.shown.findIndex((task) => task.id === taskId)
          if (at < 0) return topic
          return {
            ...topic,
            my_tasks: {
              ...my,
              shown: my.shown.map((task, i) => (i === at ? { ...task, title: updated.title } : task)),
            },
          }
        })
      )
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.rename'))
    }
  }

  // 乐观：本地这一行立刻变成新的状态——菜单、列表、rail 当场就是改后的样子，省掉的
  // 是「点一下到界面动」之间那一次往返。服务端那份回来覆盖它；失败时还原并说一声。
  async function setStatus(topicId: string, status: Topic['status'], write: () => Promise<Topic>, failed: string) {
    const topic = topics.value.find((row) => row.id === topicId)
    // 归档的正是 rail 记着的那一个：就地忘掉（归档的话题还在清单里，读清单时的检查
    // 找不到它）。失败时这一步要退回去。
    const held = topic ? lastPlaceByProject.value[topic.project_id] : undefined
    const wasRemembered = status === 'archived' && !!topic && held?.room === topicId
    if (topic) {
      await patchRows(topicId, (row) => ({ ...row, status }))
      if (wasRemembered) forgetPlace(topic.project_id)
    }
    try {
      const updated = await write()
      await patchRows(topicId, (row) => ({ ...row, ...updated }))
      void refreshTopics()
    } catch (e) {
      if (topic) {
        await patchRows(topicId, (row) => ({ ...row, status: topic.status }))
        if (wasRemembered && held) rememberPlace(topic.project_id, held)
      }
      reportError(e, failed)
    }
  }

  function archive(topicId: string): Promise<void> {
    return setStatus(topicId, 'archived', () => archiveTopic(topicId), t('shell.workspaceErrors.archive'))
  }

  function unarchive(topicId: string): Promise<void> {
    return setStatus(topicId, 'active', () => unarchiveTopic(topicId), t('shell.workspaceErrors.unarchive'))
  }

  /** 删掉一个频道（清单第 2 条）。和归档不一样，这里没有乐观的一步：删了就是没了，
   *  本地那一行等它真的走完再摘。回来 true 表示行已经删掉了，调用方该离开这一页。 */
  async function removeChannel(topicId: string): Promise<boolean> {
    const topic = topics.value.find((row) => row.id === topicId)
    try {
      await deleteTopic(topicId)
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.deleteChannel'))
      return false
    }
    if (topic && lastPlaceByProject.value[topic.project_id]?.room === topicId) forgetPlace(topic.project_id)
    await patchTopics(pid.value, (rows) => rows.filter((row) => row.id !== topicId))
    void refreshTopics()
    return true
  }

  // The three ways a topic is born. Each returns the new topic so the caller can
  // navigate to it — creating a topic without opening it is never what was meant.
  // 房间是个群聊，建出来时坐着项目的默认队友；要请别的队友进来，和请人一样走
  // 成员名册。
  async function create(title: string, description = '', membersOnly = false): Promise<Topic | null> {
    const project = projectId.value
    const name = title.trim()
    if (!project || !name) return null
    try {
      const topic = await createTopic(project, name, description.trim() || undefined, membersOnly)
      if (projectId.value !== project) return null
      await patchTopics(project, (rows) => [topic, ...rows.filter((row) => row.id !== topic.id)])
      void refreshTopics()
      return topic
    } catch (e) {
      if (projectId.value === project) reportError(e, t('shell.workspaceErrors.createTopic'))
      return null
    }
  }

  /** 转为任务：主线上一条消息的支线原地转成任务，或支线里的一条回复另开一件。返回那件任务。 */
  async function upgradeMessage(messageId: string): Promise<RoomTask | null> {
    try {
      const made = await upgradeBlock(messageId)
      await refreshTopics()
      return made
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.convertMessage'))
      return null
    }
  }

  return {
    setNotifyLevel,
    describe,
    setMembersOnly,
    setSpeakers,
    setJoined,
    markAllRead,
    markRead,
    markDmRead,
    renameTopic,
    renameTask,
    archive,
    unarchive,
    removeChannel,
    create,
    upgradeMessage,
  }
}
