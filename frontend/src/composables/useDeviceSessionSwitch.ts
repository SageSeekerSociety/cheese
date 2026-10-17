// 「现在的分布」里一台自有设备上跑着哪些 agent 会话，以及把其中一条换到别的环境去。
//
// 两条接口：`GET /projects/{id}/device-sessions` 列出这台机器上的会话（和「还有几条在
// 你打不开的频道里」那个数），`PUT` 换一条会话的环境 —— 换之前后端先尽力推送一次，
// 推没推上去都换；正在干活的会被拒绝（`SessionWorking`），除非明确说了不等它。
//
// 拆自 `components/DeviceSessionsSwitch.vue`：取数和动作在这一层，组件只管画和接线
// —— 组件不直接碰 API 层（`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'
import type { Environment } from '@/types/compute'
import type { DeviceSession } from '@/types/deviceSessions'

import { ref, toValue } from 'vue'

import { listDeviceSessions, setSessionEnvironment } from '@/api'
import { t } from '@/i18n'

export function useDeviceSessionSwitch(projectId: MaybeRefOrGetter<string>, deviceId: MaybeRefOrGetter<string>) {
  const sessions = ref<DeviceSession[]>([])
  /** 名字在这个项目里读不到、因此列不出来的会话有几条。 */
  const hidden = ref(0)
  const loading = ref(false)
  /** 列不回来时的理由；空串是「没有出错」。 */
  const error = ref('')

  /** 问一次这台机器上现在有哪些会话。 */
  async function load(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const result = await listDeviceSessions(toValue(projectId), toValue(deviceId))
      sessions.value = result.sessions
      hidden.value = result.hidden
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t('work.bulkSwitch.loadFailed')
    } finally {
      loading.value = false
    }
  }

  /** 会话所在的那段对话：房间自己的，或者某个任务的。 */
  function conversationOf(session: DeviceSession): string {
    return session.task_id ?? session.topic_id
  }

  /**
   * 把一条会话换到 `env` 去。**失败会抛出来**：一条一条换，哪一条没换成由调用方按行
   * 说（那一行上写的才是人关心的），而且一条失败不影响下一条。
   */
  async function switchSession(session: DeviceSession, env: Environment): Promise<void> {
    await setSessionEnvironment(conversationOf(session), session.id, env, { ifIdle: true })
  }

  return { sessions, hidden, loading, error, load, switchSession }
}
