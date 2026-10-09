// 任务页要的那份任务：它本身、负责人能做的几件事（开始、转交、关闭）、做它的电脑，
// 以及「与开始时相比」。对话不在这里：任务的对话就是房间那一栏，换了一段会话来读。
import type { RoomTask, TopicMemberRow } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { ApiError } from '@/api'
import { closeTask, compareDocumentVersions, renameTask, reopenTask, startTask, updateTask } from '@/api/tasks'
import { t } from '@/i18n'
import { patchQuery, queryClient } from '@/lib/queryClient'
import { myHandle } from '@/me'
import { keys } from '@/queries/keys'
import { computeProfileQuery, roomTaskQuery } from '@/queries/room'

export interface TaskComparison {
  before: string
  after: string
}

export function useTaskPage(opts: { taskId: () => string | undefined; people: () => TopicMemberRow[] }) {
  const ME = myHandle()
  // 别处已经读过这一件（侧栏、任务清单、对话里那一块带着的）就先照着它画，读回来再原地
  // 换——不先清空成一个转圈（`queries/room`）。
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

  /** 改动的回答就是更新的那一份：直接写进去。正在路上的那次读是改之前发出的，作废。 */
  function apply(changed: RoomTask) {
    void patchQuery<RoomTask>(keys.roomTask(changed.id), (held) => ({ ...held, ...changed }))
  }

  /**
   * 再读一次这件任务。`changed`：刚有了会改变它的事（推送说任务变了、一轮做完），正在
   * 路上的那次读是那之前发出的，作废另读；否则正在读的那一次还没回来就等它。
   */
  async function load(changed = false) {
    if (opts.taskId()) await read.refetch({ cancelRefetch: changed })
  }

  /** 换到另一件任务：上一件的报错、比较、电脑都不带过来。 */
  function reset() {
    machineWanted.value = false
    comparing.value = false
    comparison.value = null
    startError.value = null
    actionError.value = null
  }

  const isOwner = computed(() => !!task.value && task.value.owner_handle === ME)
  // 负责人和协作者都在做这件事：都能在任务里说话。开始、关闭、转交只归负责人。
  const takesPart = computed(
    () => isOwner.value || (!!task.value && (task.value.contributor_handles ?? []).includes(ME))
  )
  const isOpen = computed(() => task.value?.status === 'open')
  // 能接这件事、能来协作的人：项目里的人（不含 AI 队友）。
  const people = computed(() => opts.people().filter((m) => !m.agent))

  // ---- 开始 ----
  // 项目没有默认审阅人时，开始会被拒，负责人在页头下面指定一位再开始。
  const starting = ref(false)
  const startError = ref<string | null>(null)
  async function start(reviewer: string | null) {
    if (!task.value || starting.value) return
    starting.value = true
    startError.value = null
    try {
      apply(await startTask(task.value.id, reviewer))
    } catch (e) {
      startError.value = e instanceof ApiError && e.message ? e.message : t('work.task.startFailed')
    } finally {
      starting.value = false
    }
  }

  // ---- 负责人：转交、关闭、重新打开 ----
  const actionError = ref<string | null>(null)
  async function close(conclusion: string): Promise<boolean> {
    if (!task.value) return false
    actionError.value = null
    try {
      apply(await closeTask(task.value.id, conclusion.trim() || undefined))
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
      apply(await reopenTask(task.value.id))
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
      apply(await updateTask(task.value.id, { owner_handle: owner }))
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
      apply(await renameTask(task.value.id, name))
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
      apply(await updateTask(task.value.id, { contributor_handles: handles }))
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
      apply(await updateTask(task.value.id, { agent_handle: handle }))
      return true
    } catch (e) {
      actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
      return false
    }
  }

  // ---- 做它的电脑：负责人那一格点开时才读（和房间名册里那一行同一份）----
  const machineWanted = ref(false)
  const machineRead = useQuery(
    computed(() => {
      const id = task.value?.id ?? ''
      return { ...computeProfileQuery(id), enabled: !!id && machineWanted.value }
    }),
    queryClient
  )
  const machine = computed<TopicComputeProfile | null>(() =>
    machineWanted.value ? machineRead.data.value ?? null : null
  )
  const machineError = computed(() => machineWanted.value && machineRead.isError.value)
  async function loadMachine() {
    if (!task.value) return
    if (!machineWanted.value) machineWanted.value = true
    else await machineRead.refetch()
  }

  // ---- 与开始时相比 ----
  const comparing = ref(false)
  const comparison = ref<TaskComparison | null>(null)
  const compareError = ref<string | null>(null)
  async function toggleCompare() {
    if (comparing.value) {
      comparing.value = false
      return
    }
    const current = task.value
    if (!current?.document_id || current.started_doc_version == null) return
    comparing.value = true
    comparison.value = null
    compareError.value = null
    try {
      const got = await compareDocumentVersions(current.document_id, current.started_doc_version)
      comparison.value = { before: got.before.content, after: got.after.content }
    } catch {
      compareError.value = t('work.task.compareFailed')
    }
  }

  return {
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
    starting,
    startError,
    start,
    actionError,
    close,
    reopen,
    handOver,
    rename,
    machine,
    machineError,
    loadMachine,
    comparing,
    comparison,
    compareError,
    toggleCompare,
  }
}
