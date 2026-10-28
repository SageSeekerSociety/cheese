// 这一页的标题，和横跨对话与面板的那一条页头要的参数。
//
// 手机顶栏写的是当前页的标题，而这一页的标题是话题名——路由上没有，只有打开了才知道。
// 桌面顶栏不显示它，但浏览器标签页同样受益。
//
// 页头在任务页上是任务那一条：同一个高度，写着它在哪个房间、谁负责。桌面上它画在页面
// 里，手机上它就是顶栏那一行（`useScreenHeader`，由页面注册）。绑定的两份参数在这里
// 拼好：任务那一半（这件任务和能对它做的事）来自任务页那一份，另一半——在哪个房间、
// 谁在场、面板开没开——是这一页的。
import type { ComputedRef, Ref } from 'vue'
import type { RoomTask, Topic, TopicMemberRow } from '@/cx_types'
import type { TopicPlace } from './shared'

import { computed, onUnmounted, watch } from 'vue'

import { usePageTitle } from '@/composables/usePageTitle'

import { t } from '@/i18n'
import { mayIn } from '@/lib/projectPermissions'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'

/** 上面那两份绑定绑给 `TaskHeader` / `TopicHeader`，属性名一个都不能少：任务页那一半
 *  带过来的是哪几格，页面绑的时候就得是哪几格。所以带上它的类型，而不是擦成
 *  `Record<string, unknown>`——擦掉之后每一格都是 unknown，绑给页头一个都对不上。 */
export function useTopicHeader<TaskHalf extends Record<string, unknown>>(opts: {
  place: () => TopicPlace
  room: () => Topic | null
  /** 任务页那一份任务，和它给页头的那一半参数。 */
  task: ComputedRef<RoomTask | null>
  taskHeaderProps: ComputedRef<TaskHalf>
  memberNames: ComputedRef<Record<string, string>>
  roomAgents: ComputedRef<TopicMemberRow[]>
  roomConnected: Ref<boolean>
  panelOpen: Ref<boolean>
  backToRoom: () => void
  togglePanel: () => void
  openTopic: (topicId: string) => void
  /** 自己是谁：页头上「我」那一位。 */
  me: string
}) {
  const store = useWorkspaceStore()

  const { setDynamicTitle, clearDynamicTitle } = usePageTitle()
  watch(
    () => [opts.room(), opts.task.value, opts.place().threadId] as const,
    ([topic, task, threadId]) => {
      // 频道和它的支线是同一个组件：从频道进支线时它不重挂，所以支线这一条要跟着地址走。
      if (threadId) setDynamicTitle(t('work.room.thread.title'), 'workspace-thread')
      else clearDynamicTitle('workspace-thread')
      if (opts.place().taskId && task) setDynamicTitle(taskTitle(task), 'workspace-topic')
      else if (topic) setDynamicTitle(topicTitle(topic), 'workspace-topic')
      else clearDynamicTitle('workspace-topic')
    },
    { immediate: true }
  )
  onUnmounted(() => clearDynamicTitle('workspace-topic'))
  onUnmounted(() => clearDynamicTitle('workspace-thread'))

  const taskHeaderBinding = computed(() => {
    const room = opts.room()
    if (!room) return null
    return {
      ...opts.taskHeaderProps.value,
      room,
      memberNames: opts.memberNames.value,
      agentName: store.agentName,
      agents: opts.roomAgents.value,
      worksTasks: mayIn(
        store.projects.find((project) => project.id === room.project_id),
        'tasks'
      ),
      connected: opts.roomConnected.value,
      panelOpen: opts.panelOpen.value,
      'onOpen-room': opts.backToRoom,
      'onToggle-panel': opts.togglePanel,
    }
  })
  const topicHeaderBinding = computed(() => {
    const topic = opts.room()
    if (!topic) return null
    return {
      topic,
      members: store.members,
      me: opts.me,
      connected: opts.roomConnected.value,
      panelOpen: opts.panelOpen.value,
      'onToggle-panel': opts.togglePanel,
      'onOpen-topic': opts.openTopic,
      onRename: (title: string) => store.renameTopic(opts.place().topicId, title),
    }
  })

  return { taskHeaderBinding, topicHeaderBinding, setDynamicTitle }
}
