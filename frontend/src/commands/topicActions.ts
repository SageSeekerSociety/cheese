// 对一个频道能做什么。侧栏那一行的右键（和手机上长按升起的面板）、命令面板里按 Tab
// 列出来的、手机频道页顶栏的 ⋯，列的是同一份，答案只在这里写一次：给频道加一件能做
// 的事，这几处同时有。
//
// 分四组，组间一条分隔线：先是只关我自己的（标为已读、通知、复制链接），再是任务，再是
// 频道本身（详情、重命名），最后是进出。归档、取消归档、删除不在这里，在频道详情的
// 「设置」里。
//
// 重命名的做法各处不一样——侧栏里是就地改，页头另起一张对话框，命令面板里是在输入框里
// 改——所以由调用的地方传进来。频道详情也一样：页头上就地打开，别处先去那个频道。
import type { Router } from 'vue-router'
import type { Topic } from '@/cx_types'
import type { MenuCommand } from '.'

import { reactive } from 'vue'

import { requestChannelDetails } from './channelDetails'
import { copyLink, linkOf } from './copy'
import { notifyChoices, notifyLevelName } from './notifyChoices'

import { createRoomTask } from '@/api/tasks'
import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { toast } from '@/lib/toast'
import { useWorkspaceStore } from '@/stores/workspace'

export interface TopicActionHandlers {
  rename: () => void
  /** 打开频道详情。不给就先去这个频道，由它的页头打开。 */
  details?: () => void
}

/** 把几组连成一份：每组第一项前面画分隔线（第一组除外），空的组不留线。 */
function grouped(groups: MenuCommand[][]): MenuCommand[] {
  const out: MenuCommand[] = []
  for (const group of groups) {
    if (!group.length) continue
    out.push(...group.map((command, i) => (i === 0 && out.length ? { ...command, divider: true } : command)))
  }
  return out
}

export function topicActions(topic: Topic, router: Router, on: TopicActionHandlers): MenuCommand[] {
  const store = useWorkspaceStore()
  const archived = topic.status === 'archived'
  const general = topic.kind === 'root'
  const here = { name: 'workspace-topic', params: { projectId: topic.project_id, topicId: topic.id } }

  const mine: MenuCommand[] = []
  if (store.unreadMap[topic.id])
    mine.push({
      id: 'topic.markRead',
      title: t('work.room.menu.markRead'),
      icon: 'mdi-check-all',
      run: () => store.markRead(topic.id),
    })
  // 通知：只有频道里的人有档位可选，归档了不再有新消息。
  if (topic.joined && !archived) {
    const level = store.levelOf(topic.id)
    const until = store.mutedUntil(topic.id)
    mine.push({
      id: 'topic.notify',
      title: t('work.channel.notify.title'),
      icon: level === 'mute' ? 'mdi-bell-off-outline' : 'mdi-bell-outline',
      value: notifyLevelName(level, until),
      children: notifyChoices(level, until, (next, at) => void store.setNotifyLevel(topic.id, next, at)),
    })
  }
  mine.push({
    id: 'topic.copyLink',
    title: t('work.room.menu.copyLink'),
    icon: 'mdi-link-variant',
    run: () => void copyLink(linkOf(router, here)),
  })

  const tasks: MenuCommand[] = []
  // 没归档的频道（包括综合）都能新建任务。
  if (!archived)
    tasks.push({
      id: 'topic.newTask',
      title: t('work.room.menu.newTask'),
      icon: 'mdi-checkbox-marked-circle-plus-outline',
      run: () => void newTask(topic, router),
    })
  // 全部任务：这个频道的任务一整页，进行中的、已结束的都在，按谁的筛。侧栏频道下面只
  // 挂几条，要看全部从这里走。
  tasks.push({
    id: 'topic.allTasks',
    title: t('work.room.menu.allTasks'),
    icon: 'mdi-format-list-checks',
    run: () =>
      void router.push({
        name: 'project-tasks',
        params: { projectId: topic.project_id },
        query: { channel: topic.id },
      }),
  })

  const itself: MenuCommand[] = [
    {
      id: 'topic.details',
      title: t('work.channelDetails.title'),
      icon: 'mdi-information-outline',
      run:
        on.details ??
        (() => {
          requestChannelDetails(topic.id)
          void router.push(here)
        }),
    },
  ]
  // 改名：项目里谁都能改。频道叫什么是用它的这些人一起定的事，不是创建者一个人的；
  // 后端那一侧同样只认「你能进这个频道」（`topics_title.py`）。「综合」是项目本身，
  // 不改名；归档了的也不再改。
  if (!general && !archived)
    itself.push({ id: 'topic.rename', title: t('work.room.menu.rename'), icon: 'mdi-pencil-outline', run: on.rename })

  // 「综合」是项目本身：人人都在，退不出去。
  const membership: MenuCommand[] = []
  if (!general && topic.joined)
    membership.push({
      id: 'topic.leave',
      title: t('work.channel.leave'),
      icon: 'mdi-logout-variant',
      run: () => void store.setJoined(topic.id, false),
    })
  else if (!general && !archived)
    membership.push({
      id: 'topic.join',
      title: t('work.channel.join'),
      icon: 'mdi-login-variant',
      run: () => void store.setJoined(topic.id, true),
    })

  return grouped([mine, tasks, itself, membership])
}

/** 正在新建任务的频道。一个频道同一时刻只建一件：连点「新建任务」（或从几处入口各点一
 *  下）不会建出好几件空任务。建好、进了任务页才放开。 */
const creatingIn = reactive(new Set<string>())

/** 这个频道里的新建任务还在路上：入口的按钮转圈、点不动。 */
export function creatingTaskIn(topicId: string | null | undefined): boolean {
  return !!topicId && creatingIn.has(topicId)
}

/** 新建任务：建的人就是负责人，建好就去任务页，在那里和 AI 队友说清楚要做什么。 */
export async function newTask(topic: Topic, router: Router): Promise<void> {
  if (creatingIn.has(topic.id)) return
  creatingIn.add(topic.id)
  const store = useWorkspaceStore()
  try {
    const task = await createRoomTask(topic.id)
    await router.push({
      name: 'workspace-task',
      params: { projectId: topic.project_id, topicId: topic.id, taskId: task.id },
    })
  } catch (e) {
    store.reportError(e, t('work.room.menu.newTaskFailed'))
  } finally {
    creatingIn.delete(topic.id)
  }
}

/** 归档一个话题。正开着的就是它的话，回到项目首页：不把一个冻住的房间留在内容区。 */
export async function archiveTopic(topic: Topic, router: Router): Promise<void> {
  const store = useWorkspaceStore()
  await store.archive(topic.id)
  // 归档可能失败（store 只记下错误、不抛）：本地这一行没变成 archived 就什么都不做。
  if (store.topics.find((row) => row.id === topic.id)?.status !== 'archived') return
  const here = router.currentRoute.value
  if (here.name === 'workspace-topic' && routeIds(here.params).topicId === topic.id)
    void router.replace({ name: 'workspace-project', params: { projectId: topic.project_id } })
  // 归档是就地发生的一件事：不说一声，人就以为菜单点错了、房间凭空没了。撤销把刚
  // 才那一下退回去——时间给够，因为它要人读一句再决定是不是点。
  toast(t('work.room.archiveNotice.done'), {
    duration: 6000,
    action: { label: t('work.room.archiveNotice.undo'), onClick: () => void store.unarchive(topic.id) },
  })
}
