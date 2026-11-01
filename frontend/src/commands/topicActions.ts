// 对一个话题能做什么。话题列表那一行的 ⋯、手机上长按一行升起的面板、房间顶栏的
// ⋯、命令面板里按 Tab 列出来的，问的是同一个问题，答案只在这里写一次：给话题加一件
// 能做的事，这几处同时有。
//
// 重命名的做法各处不一样——侧栏里是就地改，房间里另起一页，命令面板里是在输入框里
// 改——所以由调用的地方传进来。复制链接、标为已读、静音、归档在哪都一样，写在这里。
import type { Router } from 'vue-router'
import type { Topic } from '@/cx_types'
import type { MenuCommand } from '.'

import { copyLink, linkOf } from './copy'

import { createRoomTask } from '@/api/tasks'
import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { toast } from '@/lib/toast'
import { useWorkspaceStore } from '@/stores/workspace'

export interface TopicActionHandlers {
  rename: () => void
  /** 「删除频道」（清单第 2 条）。没人接这一条就不画它——删除要走二次确认，而那张
   *  确认框由调用的地方出。 */
  remove?: () => void
}

export function topicActions(topic: Topic, router: Router, on: TopicActionHandlers): MenuCommand[] {
  const store = useWorkspaceStore()
  const actions: MenuCommand[] = [
    {
      id: 'topic.copyLink',
      title: t('work.room.menu.copyLink'),
      icon: 'mdi-link-variant',
      run: () =>
        void copyLink(
          linkOf(router, { name: 'workspace-topic', params: { projectId: topic.project_id, topicId: topic.id } })
        ),
    },
  ]
  if (store.unreadMap[topic.id])
    actions.push({
      id: 'topic.markRead',
      title: t('work.room.menu.markRead'),
      icon: 'mdi-check-all',
      run: () => store.markRead(topic.id),
    })
  // 静音：只有直接 @我才计数和通知，直到我取消。要静音一段时间、或者要所有新消息，
  // 在频道顶栏的铃铛里选。
  if (topic.joined) {
    const muted = store.isMuted(topic.id)
    actions.push({
      id: muted ? 'topic.unmute' : 'topic.mute',
      title: muted ? t('work.room.menu.unmute') : t('work.room.menu.mute'),
      icon: muted ? 'mdi-bell-outline' : 'mdi-bell-off-outline',
      run: () => void store.setNotifyLevel(topic.id, muted ? 'mentions' : 'mute'),
    })
  }
  // 没归档的房间（包括综合）都能新建任务。
  if (topic.status !== 'archived') {
    actions.push({
      id: 'topic.newTask',
      title: t('work.room.menu.newTask'),
      icon: 'mdi-checkbox-marked-circle-plus-outline',
      run: () => void newTask(topic, router),
    })
  }
  // 全部任务：这个频道的任务一整页，进行中的、已结束的都在，按谁的筛。侧栏频道下面只
  // 挂几条，展开也只到进行中的，要看全部从这里走。
  actions.push({
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
  // 文件：这个频道里的文件都在右侧面板的「文件」那一格——发进来的附件、芝士交付的和
  // 写的、定时任务交回的。看是随时能看的，归档了也在，所以不问状态。
  actions.push({
    id: 'topic.files',
    title: t('work.room.menu.files'),
    icon: 'mdi-folder-outline',
    run: () =>
      void router.push({
        name: 'workspace-topic',
        params: { projectId: topic.project_id, topicId: topic.id },
        query: { tab: 'files' },
      }),
  })
  // 「综合」是项目本身：人人都在，退不出去，也不能改名、归档。
  if (topic.kind === 'root') return actions
  if (topic.joined)
    actions.push({
      id: 'topic.leave',
      title: t('work.channel.leave'),
      icon: 'mdi-logout-variant',
      run: () => void store.setJoined(topic.id, false),
    })
  else if (topic.status !== 'archived')
    actions.push({
      id: 'topic.join',
      title: t('work.channel.join'),
      icon: 'mdi-login-variant',
      run: () => void store.setJoined(topic.id, true),
    })
  // 删除排在最后：它不可撤销，不该挨着上面随手点的那几件。已归档的也能删——归档只是
  // 不上不下地搁着，删除才是让它真的走。
  const remove: MenuCommand[] = on.remove
    ? [
        {
          id: 'topic.delete',
          title: t('work.room.menu.delete'),
          icon: 'mdi-trash-can-outline',
          danger: true,
          run: on.remove,
        },
      ]
    : []
  if (topic.status === 'archived') {
    // 已归档的不再改名，只剩「取消归档」和「删除」——两件都归管这个频道的人。
    if (!topic.can_manage) return actions
    actions.push({
      id: 'topic.unarchive',
      title: t('work.room.menu.unarchive'),
      icon: 'mdi-archive-arrow-up-outline',
      run: () => void store.unarchive(topic.id),
    })
    return [...actions, ...remove]
  }
  // 改名：项目里谁都能改。频道叫什么是用它的这些人一起定的事，不是创建者一个人的；
  // 后端那一侧同样只认「你能进这个频道」（`topics_title.py`）。
  actions.push({ id: 'topic.rename', title: t('work.room.menu.rename'), icon: 'mdi-pencil-outline', run: on.rename })
  // 归档、取消归档只给管这个频道的人：创建者和项目管理员。有进行中的任务时不归档。
  if (!topic.can_manage) return actions
  if ((topic.my_tasks?.open ?? 0) === 0) {
    actions.push({
      id: 'topic.archive',
      title: t('work.room.menu.archive'),
      icon: 'mdi-archive-arrow-down-outline',
      run: () => void archiveTopic(topic, router),
    })
  }
  return [...actions, ...remove]
}

/** 新建任务：建的人就是负责人，建好就去任务页，在那里和 AI 队友说清楚要做什么。 */
export async function newTask(topic: Topic, router: Router): Promise<void> {
  const store = useWorkspaceStore()
  try {
    const task = await createRoomTask(topic.id)
    void router.push({
      name: 'workspace-task',
      params: { projectId: topic.project_id, topicId: topic.id, taskId: task.id },
    })
  } catch (e) {
    store.reportError(e, t('work.room.menu.newTaskFailed'))
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
