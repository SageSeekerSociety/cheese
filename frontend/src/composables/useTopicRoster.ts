// 一个频道里「有谁」这一格要的两样东西：名册本身（加人、移人两条动作），以及每位
// AI 队友这条会话跑在哪个环境里。
//
// 名册读的是 `query/room` 里那一份（房间头部、对话栏的 @ 候选、成员面板读的是同一份，
// 写名册的接口自己会把它标过期）；环境那一份同理。动作在这一层，组件只管画和接线
// —— 组件不直接碰 API 层（`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'
import type { SessionEnvironment, TopicEnvironment } from '@/types/compute'
import type { Environment } from '@/types/compute'

import { computed, onBeforeUnmount, onMounted, ref, toValue } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { addTopicMember, removeTopicMember, setEnvironmentDefault, setSessionEnvironment } from '@/api'
import { t } from '@/i18n'
import { whenIdle } from '@/lib/idle'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { environmentQuery, roomMembersQuery } from '@/query/room'

export function useTopicRoster(topicId: MaybeRefOrGetter<string>) {
  // ---- 名册 -------------------------------------------------------------------

  // 切回来过的房间先画上次那份名册，背后再重取（`query/room`）。骨架只在手上什么
  // 都没有时出：有上次那份就照着它画，回来了原地换。
  const roster = useQuery(
    computed(() => roomMembersQuery(toValue(topicId))),
    queryClient
  )
  const members = computed(() => roster.data.value?.data ?? [])
  const loading = computed(() => roster.isPending.value && !roster.isError.value)

  /** 「名册没读到」那一条的理由，空串是没出错。 */
  const loadError = computed(() =>
    roster.error.value ? roster.error.value.message || t('work.room.roster.loadFailed') : ''
  )

  /** 改名册那一下没成，压过「名册没读到」：那是此刻手上这一下的结果。 */
  const actionError = ref('')
  const busy = ref(false)

  /**
   * 改一次名册：加人或移人，成败都等新的一份名册回来再收手
   * （写名册的接口已经把名册标过期了）。失败把理由留在 `actionError` 里。
   */
  async function guard(fn: () => Promise<unknown>): Promise<void> {
    busy.value = true
    actionError.value = ''
    try {
      await fn()
      await roster.refetch()
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : t('work.room.roster.failed')
    } finally {
      busy.value = false
    }
  }

  /** 把 `handle` 加进这个频道。 */
  async function addMember(handle: string): Promise<void> {
    await guard(() => addTopicMember(toValue(topicId), handle))
  }

  /** 把 `handle` 移出这个频道。 */
  async function removeMember(handle: string): Promise<void> {
    await guard(() => removeTopicMember(toValue(topicId), handle))
  }

  // ---- 执行环境 ---------------------------------------------------------------

  // 一次读回新会话默认用哪个、每条会话各在哪个环境里（和任务页那一格同一份）。
  // 它只喂两处：名册展开后的那一行，和页头那个「能访问整台机器」的标记。名册一展开就
  // 读，所以进房间这一下没必要挤在首屏前 —— 推到浏览器空下来再问，标记晚一点补上。
  const machinesWanted = ref(false)
  const machinesRead = useQuery(
    computed(() => ({ ...environmentQuery(toValue(topicId)), enabled: machinesWanted.value && !!toValue(topicId) })),
    queryClient
  )
  const machines = computed<TopicEnvironment | null>(() => machinesRead.data.value ?? null)
  const machinesError = computed(() =>
    machinesRead.error.value ? machinesRead.error.value.message || t('compute.env.loadFailed') : ''
  )
  /** 名册一打开就调用：第一趟开读，之后每一趟都重取。 */
  function loadMachines(): void {
    if (!machinesWanted.value) machinesWanted.value = true
    else void queryClient.invalidateQueries({ queryKey: keys.roomEnvironment(toValue(topicId)) })
  }

  /** 换一位 AI 队友这条会话的环境；`EnvironmentMenu` 要的是「环境进、请求出」。 */
  function saveSession(session: SessionEnvironment) {
    return (env: Environment) => setSessionEnvironment(toValue(topicId), session.session_id, env)
  }

  /** 换这个频道新会话默认用的环境。 */
  function saveDefault(env: Environment) {
    return setEnvironmentDefault(toValue(topicId), env)
  }

  let cancelIdleLoad: (() => void) | null = null
  onMounted(() => {
    cancelIdleLoad = whenIdle(() => (machinesWanted.value = true))
    window.addEventListener('project-compute-updated', loadMachines)
  })
  onBeforeUnmount(() => {
    cancelIdleLoad?.()
    window.removeEventListener('project-compute-updated', loadMachines)
  })

  return {
    members,
    loading,
    loadError,
    busy,
    actionError,
    addMember,
    removeMember,
    machines,
    machinesError,
    loadMachines,
    saveSession,
    saveDefault,
  }
}
