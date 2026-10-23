// 帮助菜单里的「界面导览」：导览只从项目「综合」的主线上开始（第一站圈的是那里的消息框
// 和发送键），在别的页面上点它，什么都不会出现在屏幕上（见 `useProjectTour.ts`：每一站
// 的目标得当下就登记在页面里）。
//
// 所以点菜单时先把人带到那一页再开始。不在项目里、或者项目没有能用的「综合」（归档了、
// 权限打不开、清单没读到）时，说一句该去哪儿点——「点了没反应」是这一项最要命的坏法。
import type { Topic } from '@/cx_types'

import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { replayTour } from './useProjectTour'

import { t } from '@/i18n'
import { isUuid, projectRefusal, routeIds } from '@/lib/addresses'
import { useWorkspaceStore } from '@/stores/workspace'

/** 频道清单还没读到时等它一会儿（刚打开项目的那一瞬）。等不到就照「没有」说，不晾着人。 */
const WAIT_FOR_TOPICS_MS = 2000

export function useInterfaceTour() {
  const route = useRoute()
  const router = useRouter()
  const workspace = useWorkspaceStore()

  /** 这个项目此刻读到的「综合」；读的不是它（刚切过去、清单还没到）就是 null。 */
  function generalOf(projectId: string): Topic | null {
    return workspace.projectId === projectId ? workspace.rootTopic : null
  }

  /** 打开这个项目、等它的频道清单到货，回它那份「综合」。 */
  function openGeneral(projectId: string): Promise<Topic | null> {
    workspace.openProject(projectId)
    const known = generalOf(projectId)
    if (known) return Promise.resolve(known)
    return new Promise((resolve) => {
      let stop = () => {}
      const timer = setTimeout(() => {
        stop()
        resolve(null)
      }, WAIT_FOR_TOPICS_MS)
      stop = watch(
        () => generalOf(projectId),
        (general) => {
          if (!general) return
          clearTimeout(timer)
          stop()
          resolve(general)
        }
      )
    })
  }

  async function start() {
    const param = route.params.projectId
    const ref = typeof param === 'string' && param ? param : null
    if (!ref) {
      toast(t('navigation.feedback.tourStartsInGeneral'))
      return
    }
    // 地址上的项目是短名（`/projects/cheese/...`），页面和接口认的是 UUID（`lib/addresses`
    // 的守卫已经把这个短名换成 UUID 记在表里）。拿短名去 `openProject`，频道清单会被服务端
    // 当成参数不合法挡回来：侧栏只剩一句报错，人还停在原地。
    const projectId = routeIds(route.params).projectId
    if (!projectId || !isUuid(projectId)) {
      // 短名换不出来（项目不存在、或者看不到）：这一页自己就会说打不开，别再发那个注定
      // 被挡回来的请求。只是还没换出来（守卫没跑过）时，照旧说一句该去哪儿点。
      if (!projectRefusal(ref)) toast(t('navigation.feedback.tourStartsInGeneral'))
      return
    }
    const general = await openGeneral(projectId)
    if (!general || general.status === 'archived') {
      toast(t('navigation.feedback.tourStartsInGeneral'))
      return
    }
    await router.push({ name: 'workspace-topic', params: { projectId, topicId: general.id } })
    replayTour()
  }

  return { start }
}
