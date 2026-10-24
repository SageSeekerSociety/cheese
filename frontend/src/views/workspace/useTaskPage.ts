// 任务页要的那份任务：它本身、负责人能做的几件事（转交、关闭）和做它的电脑。对话不在
// 这里：任务的对话就是房间那一栏，换了一段会话来读。
import type { RoomTask, TopicMemberRow } from '@/cx_types'
import type { Environment, TopicEnvironment } from '@/types/compute'

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { ApiError, setEnvironmentDefault, setSessionEnvironment } from '@/api'
import { closeTask, renameTask, reopenTask, updateTask } from '@/api/tasks'
import { copyTaskLink } from '@/commands/taskLink'
import { t } from '@/i18n'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { environmentQuery, roomTaskQuery } from '@/query/room'

export function useTaskPage(opts: { taskId: () => string | undefined; people: () => TopicMemberRow[] }) {
  const ME = myHandle()
  // 别处已经读过这一件（侧栏、任务清单、对话里那一块带着的）就先照着它画，读回来再原地
  // 换——不先清空成一个转圈（`query/room`）。
  const read = useQuery(
    computed(() => {
      const id = opts.taskId() ?? ''
      return { ...roomTaskQuery(id), enabled: !!id }
    }),
    queryClient
  )
  const task = computed<RoomTask | null>(() => read.data.value ?? null)
  const loading = computed(() => !!opts.taskId() && read.isPending.value)
  const loadError = computed<string | null>(() => {
    const e = read.error.value
    if (!e || read.data.value) return null
    return e instanceof ApiError && e.status === 404 ? t('work.task.notFound') : t('work.task.loadFailed')
  })

  /**
   * 改动的回答就是更新的那一份，整行：直接写进去。正在路上的那次读是改之前发出的，
   * 作废。第一次读还没回来时（页面先照着清单里那一行画的）也写：这一行就是此刻的样子。
   */
  async function apply(changed: RoomTask) {
    const key = keys.roomTask(changed.id)
    await queryClient.cancelQueries({ queryKey: key, exact: true })
    queryClient.setQueryData<RoomTask>(key, (held) => ({ ...held, ...changed }))
  }

  /**
   * 再读一次这件任务。`changed`：刚有了会改变它的事（推送说任务变了、一轮做完），正在
   * 路上的那次读是那之前发出的，作废另读；否则正在读的那一次还没回来就等它。
   */
  async function load(changed = false) {
    if (opts.taskId()) await read.refetch({ cancelRefetch: changed })
  }

  /** 换到另一件任务：上一件的报错、电脑都不带过来。 */
  function reset() {
    machineWanted.value = false
    actionError.value = null
  }

  const isOwner = computed(() => !!task.value && task.value.owner_handle === ME)
  // 负责人和协作者都在做这件事：他们的话是给 AI 队友的指令。关闭、转交只归负责人。
  const takesPart = computed(
    () => isOwner.value || (!!task.value && (task.value.contributor_handles ?? []).includes(ME))
  )
  const isOpen = computed(() => task.value?.status === 'open')
  // 能接这件事、能来协作的人：项目里的人（不含 AI 队友）。
  const people = computed(() => opts.people().filter((m) => !m.agent))

  // ---- 转交（负责人）、关闭（负责人和协作者）、重新打开（角色有「新建任务和审阅」的人）----
  const actionError = ref<string | null>(null)
  async function close(conclusion: string): Promise<boolean> {
    if (!task.value) return false
    actionError.value = null
    try {
      await apply(await closeTask(task.value.id, conclusion.trim() || undefined))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }
  async function reopen(): Promise<boolean> {
    if (!task.value) return false
    actionError.value = null
    try {
      await apply(await reopenTask(task.value.id))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }
  async function handOver(owner: string): Promise<boolean> {
    if (!task.value || !owner) return false
    actionError.value = null
    try {
      await apply(await updateTask(task.value.id, { owner_handle: owner }))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }

  // ---- 改名：负责人和协作者都能改 ----
  async function rename(title: string): Promise<boolean> {
    const name = title.trim()
    if (!task.value || !name) return false
    actionError.value = null
    try {
      await apply(await renameTask(task.value.id, name))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }

  // ---- 协作者：负责人增减；协作者只能把自己去掉 ----
  async function setCollaborators(handles: string[]): Promise<boolean> {
    if (!task.value) return false
    actionError.value = null
    try {
      await apply(await updateTask(task.value.id, { contributor_handles: handles }))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }

  // ---- 做这件事的队友：负责人换；不选（null）就是跟着房间的那位 ----
  async function setAgent(handle: string | null): Promise<boolean> {
    if (!task.value) return false
    actionError.value = null
    try {
      await apply(await updateTask(task.value.id, { agent_handle: handle }))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }

  // ---- 它的会话在哪个环境里：负责人那一格点开时才读（和房间名册同一份）----
  const machineWanted = ref(false)
  const machineRead = useQuery(
    computed(() => {
      const id = task.value?.id ?? ''
      return { ...environmentQuery(id), enabled: !!id && machineWanted.value }
    }),
    queryClient
  )
  const machine = computed<TopicEnvironment | null>(() => (machineWanted.value ? machineRead.data.value ?? null : null))
  const machineError = computed(() => machineWanted.value && machineRead.isError.value)
  // 它的会话各换各的环境；「新会话默认」只管以后开工的。
  function setMachine(sessionId: string, env: Environment) {
    if (!task.value) return Promise.resolve({})
    return setSessionEnvironment(task.value.id, sessionId, env)
  }
  function setMachineDefault(env: Environment) {
    if (!task.value) return Promise.resolve({})
    return setEnvironmentDefault(task.value.id, env)
  }
  async function loadMachine() {
    if (!task.value) return
    if (!machineWanted.value) machineWanted.value = true
    else await machineRead.refetch()
  }

  // 任务页头部（TaskHeader）要的那一半：这件任务和能对它做的事。另一半（在哪个房间、
  // 谁在场、面板开没开）是页面的，由页面补上。
  // 复制链接要 router——组件那一层不认路由，所以地址在这里算好，交一个动作进去。
  const router = useRouter()
  const headerProps = computed(() => ({
    task: task.value,
    people: people.value,
    machine: machine.value,
    machineError: machineError.value,
    actionError: actionError.value,
    copyLink: () => {
      const row = task.value
      if (row) copyTaskLink(router, row)
    },
    close,
    reopen,
    handOver,
    rename,
    setCollaborators,
    setAgent,
    loadMachine,
    setMachine,
    setMachineDefault,
  }))

  return {
    headerProps,
    task,
    loading,
    loadError,
    load,
    reset,
    isOwner,
    takesPart,
    setCollaborators,
    setAgent,
    isOpen,
    people,
    actionError,
    close,
    reopen,
    handOver,
    rename,
    machine,
    machineError,
    loadMachine,
    setMachine,
    setMachineDefault,
  }
}
