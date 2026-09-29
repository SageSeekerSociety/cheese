// 对一个话题能做什么。话题列表那一行的 ⋯、手机上长按一行升起的面板、房间顶栏的
// ⋯ 问的是同一个问题，答案只在这里写一次：给话题加一件能做的事，这几处同时有。
//
// 做法各处不一样——侧栏里重命名是就地改，房间里是另起一页——所以怎么做由调用的
// 地方传进来，这里只说有哪几件、各叫什么。
import type { Topic } from '@/cx_types'
import type { MenuCommand } from '.'

import { t } from '@/i18n'

export interface TopicActionHandlers {
  rename: () => void
  archive: () => void
  unarchive: () => void
}

export function topicActions(topic: Topic, on: TopicActionHandlers): MenuCommand[] {
  // 项目本体不是一件事：没有名字可改，也不能归档。
  if (topic.kind === 'root') return []
  if (topic.status === 'archived') {
    // 已归档的只剩「取消归档」。
    return topic.can_archive
      ? [
          {
            id: 'topic.unarchive',
            title: t('work.room.menu.unarchive'),
            icon: 'mdi-archive-arrow-up-outline',
            run: on.unarchive,
          },
        ]
      : []
  }
  const actions: MenuCommand[] = [
    { id: 'topic.rename', title: t('work.room.menu.rename'), icon: 'mdi-pencil-outline', run: on.rename },
  ]
  if (topic.can_archive)
    actions.push({
      id: 'topic.archive',
      title: t('work.room.menu.archive'),
      icon: 'mdi-archive-arrow-down-outline',
      run: on.archive,
    })
  return actions
}
