// 项目侧栏那一半「路由」：我在哪、点一下去哪儿、指针停住先预热什么，以及话题行尾
// 那一行右键的几项（要一个真的 router 才算得出链接）。
//
// 这一层存在的理由只有一个：`src/components/**` 不许 import vue-router
//（.claude/rules/architecture.md）。侧栏仍然要路由，所以它绕这一道取——画的那几个
// 组件只收一个 `routeName` 和几个「去哪儿」的回调，宿主的地址是从哪来的与它们无关。
//
// 和数据那一半（`useTopicRail.ts`）分开，是为了让那一半能在一个没有路由的宿主里跑
// 起来：折叠记忆、红灯、分组都不需要知道 URL 长什么样。
import type { MenuCommand } from '../commands'
import type { TaskActionTask } from '../commands/taskActions'
import type { Topic } from '../cx_types'

import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { taskActions } from '../commands/taskActions'
import { copyTaskLink } from '../commands/taskLink'
import { topicActions } from '../commands/topicActions'
import { mayIn } from '../lib/projectPermissions'
import { cancelPrefetch, prefetchOnHover } from '../lib/routePrefetch'
import { myHandle } from '../me'
import { useWorkspaceStore } from '../stores/workspace'

export interface TopicRailRouteHandlers {
  /** 行菜单里的「重命名」：侧栏里是就地改，改怎么做由调用方决定。 */
  rename: (topic: Topic) => void
  /** 任务行菜单里的「重命名」：同上，只是那一条属于一件任务。 */
  renameTask: (task: TaskActionTask) => void
  /** 任务行菜单里的「关闭任务」：先要一句结论，那张确认框由侧栏出。 */
  closeTask: (task: TaskActionTask) => void
}

/**
 * @param projectId 当前项目（侧栏的地址全挂在它下面）。给 getter 而不是值：换项目
 *   就换了一整份地址，调用方那边是 props，取的时候再读。
 */
export function useTopicRailRoutes(projectId: () => string | null, on: TopicRailRouteHandlers) {
  const router = useRouter()
  const route = useRoute()
  const store = useWorkspaceStore()

  /** 当前页的名字（不是字符串就给 null）——置顶行和项目菜单凭它画选中态。 */
  const routeName = computed<string | null>(() => (typeof route.name === 'string' ? route.name : null))

  /** 打开项目里的一页（看板/资料库/…）。没选项目时什么都不做。 */
  function openPage(name: string) {
    const id = projectId()
    if (!id) return
    router.push({ name, params: { projectId: id } })
  }

  // 谁负责 push，谁负责预热：指针停住的时候把这个页面的代码先下下来，等真按下去时
  // 只剩下拉数据那一段。
  function prefetchPage(name: string) {
    const id = projectId()
    if (!id) return
    prefetchOnHover({ router, to: { name, params: { projectId: id } } })
  }

  // 换项目落在项目地址本身，而不是它的某个话题：哪个话题该开着是那个项目的事
  //（手机上这个地址就是它的话题列表，桌面上它自己跳大本营）。
  function openProject(id: string) {
    if (id === projectId()) return
    router.push({ name: 'workspace-project', params: { projectId: id } })
  }

  /** 侧栏里给一个频道做得了的事（右键、长按的面板都问它）：和命令面板、手机顶栏的 ⋯
   *  是同一份（`commands/topicActions.ts`）。 */
  function actionsFor(topic: Topic): MenuCommand[] {
    return topicActions(topic, router, { rename: () => on.rename(topic) })
  }

  /** 侧栏里给一件任务做得了的事：和任务页那一份是同一份（`commands/taskActions.ts`）。 */
  function taskActionsFor(task: TaskActionTask): MenuCommand[] {
    const id = projectId()
    return taskActions(
      task,
      {
        rename: () => on.renameTask(task),
        close: () => on.closeTask(task),
        reopen: () => void store.reopenTask(task.id),
      },
      {
        me: myHandle(),
        worksTasks: mayIn(
          store.projects.find((project) => project.id === id),
          'tasks'
        ),
        copyLink: () => copyTaskLink(router, task),
      }
    )
  }

  /** 关掉一件任务：侧栏那张确认框点了「关闭任务」之后。 */
  function closeTask(task: TaskActionTask, conclusion: string): Promise<boolean> {
    return store.closeTask(task.id, conclusion)
  }

  return { routeName, openPage, prefetchPage, openProject, cancelPrefetch, actionsFor, taskActionsFor, closeTask }
}
