// 任务页要的那份任务：它本身、负责人能做的几件事（开始、转交、关闭）、做它的电脑，
// 以及「与开始时相比」。对话不在这里：任务的对话就是房间那一栏，换了一段会话来读。
import type { RoomTask, TopicMemberRow } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { computed, ref } from 'vue'

import { ApiError, getTopicComputeProfile } from '@/api'
import { closeTask, compareDocumentVersions, getTask, renameTask, reopenTask, startTask, updateTask } from '@/api/tasks'
import { t } from '@/i18n'
import { myHandle } from '@/me'

export interface TaskComparison {
  before: string
  after: string
}

export function useTaskPage(opts: { taskId: () => string | undefined; people: () => TopicMemberRow[] }) {
  const ME = myHandle()
  const task = ref<RoomTask | null>(null)
  const loading = ref(false)
  const loadError = ref<string | null>(null)

  // 改动落下的次数。一次读在改动之前发出、在它之后才回来，带回来的是改之前的样子：
  // 刚点的「关闭」会被它盖回「进行中」，等下一次读才又跳回来。所以读发出之后有改动
  // 落下，这次读就不算数——改动的回答本身就是更新的那一份。
  let written = 0
  function apply(changed: RoomTask) {
    written += 1
    // 改的时候人已经换到别的任务上去了：回答是那一件的，不往这一件上写。
    if (task.value?.id === changed.id) task.value = { ...task.value, ...changed }
  }

  async function load(silent = false) {
    const id = opts.taskId()
    const seen = written
    if (!id) return
    if (!silent) loading.value = true
    loadError.value = null
    try {
      const payload = await getTask(id)
      if (opts.taskId() === id && seen === written) task.value = payload
    } catch (e) {
      if (opts.taskId() !== id) return
      loadError.value = e instanceof ApiError && e.status === 404 ? t('work.task.notFound') : t('work.task.loadFailed')
    } finally {
      if (opts.taskId() === id) loading.value = false
    }
  }

  function reset() {
    task.value = null
    loadError.value = null
    machine.value = null
    machineError.value = false
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

  // ---- 做它的电脑：负责人那一格点开时才读 ----
  const machine = ref<TopicComputeProfile | null>(null)
  const machineError = ref(false)
  async function loadMachine() {
    if (!task.value) return
    machineError.value = false
    try {
      machine.value = await getTopicComputeProfile(task.value.id)
    } catch {
      machineError.value = true
    }
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
