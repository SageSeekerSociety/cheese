// 对一个话题能做什么。话题列表那一行的 ⋯、手机上长按一行升起的面板、房间顶栏的
// ⋯、命令面板里按 Tab 列出来的，问的是同一个问题，答案只在这里写一次：给话题加一件
// 能做的事，这几处同时有。
//
// 重命名的做法各处不一样——侧栏里是就地改，房间里另起一页，命令面板里是在输入框里
// 改——所以由调用的地方传进来。复制链接、标为已读、归档在哪都一样，写在这里。
import type { Router } from 'vue-router'
import type { Topic } from '@/cx_types'
import type { MenuCommand } from '.'

import { copyLink, linkOf } from './copy'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export interface TopicActionHandlers {
  rename: () => void
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
  // 项目本体不是一件事：没有名字可改，也不能归档。
  if (topic.kind === 'root') return actions
  if (topic.status === 'archived') {
    // 已归档的不再改名，只剩「取消归档」。
    if (topic.can_archive)
      actions.push({
        id: 'topic.unarchive',
        title: t('work.room.menu.unarchive'),
        icon: 'mdi-archive-arrow-up-outline',
        run: () => void store.unarchive(topic.id),
      })
    return actions
  }
  actions.push({ id: 'topic.rename', title: t('work.room.menu.rename'), icon: 'mdi-pencil-outline', run: on.rename })
  if (topic.can_archive)
    actions.push({
      id: 'topic.archive',
      title: t('work.room.menu.archive'),
      icon: 'mdi-archive-arrow-down-outline',
      run: () => void archiveTopic(topic, router),
    })
  return actions
}

/** 归档一个话题。正开着的就是它的话，回到项目首页：不把一个冻住的房间留在内容区。 */
export async function archiveTopic(topic: Topic, router: Router): Promise<void> {
  const store = useWorkspaceStore()
  await store.archive(topic.id)
  const here = router.currentRoute.value
  if (here.name !== 'workspace-topic' || here.params.topicId !== topic.id) return
  if (store.topics.find((row) => row.id === topic.id)?.status === 'archived')
    void router.replace({ name: 'workspace-project', params: { projectId: topic.project_id } })
}
