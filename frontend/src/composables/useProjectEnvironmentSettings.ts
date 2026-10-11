// 「运行环境」那一块的数据与动作。
//
// 拆自 `components/ProjectEnvironmentSettings.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），取数、保存、状态轮询、「让芝士看看」/重试/采用都落在
// 这里，页面接线。和 `useBranchProtection.ts` 一个路子（`.claude/rules/architecture.md`
// 「组件不取数」）。
//
// 编辑草稿（那两段脚本与环境变量那几行）留在组件里：组件把整份 `{ setup_script,
// startup_script, variables }` 报上来，这一层照着发请求 —— 和分支保护把补丁报上来一样。
// 环境变量那几行要报的是「行」而不是「键值对」：查重（同名两行）是这一层发请求前
// 的那道关，报在保存那一行回执里（原样，见下 `save`）。
//
// `enabled`：设置页一次只画一栏。环境这一栏不光取数，还每 5 秒轮询一次现状，所以这一栏
// 没被打开过就不读、也不起表；离开这一栏就把表停掉（原来那一件离开时随卸载一起停）。
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '@/cx_types'
import type { EnvironmentDiagnosis } from '@/types/environment'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { holdRevealUntil } from '@/composables/useRevealGate'
import { useSaveState } from '@/composables/useSaveState'

import { applyRoomEnvironment, getProjectEnvironment, getRoomEnvironment, saveProjectEnvironment } from '@/api'
import { t } from '@/i18n'

/** 组件报上来的那一份配置：脚本两段，和环境变量那几行（查重在这一层做）。 */
export interface EnvironmentConfigDraft {
  setup_script: string
  startup_script: string
  variables: { key: string; value: string }[]
}

export interface EnvironmentSettingsOptions {
  /** 这一栏此刻在不在屏幕上：第一次为真才读、才起表；离开时停表。不给就是挂上即读。 */
  enabled?: () => boolean
  /** 「从一条环境准备失败点过来」带的频道：先看那个频道。 */
  room?: () => string | undefined
  /** 「让芝士看看」：问一次这个频道最近那次失败。 */
  diagnoseRoom: (roomId: string) => Promise<EnvironmentDiagnosis>
}

export function useProjectEnvironmentSettings(projectId: () => string, options: EnvironmentSettingsOptions) {
  const enabled = options.enabled ?? (() => true)
  const info = ref<ProjectEnvironmentInfo | null>(null)
  const selectedRoom = ref<string | null>(null)
  const status = ref<EnvironmentStatus | null>(null)
  const error = ref('')
  const diagnosis = ref<EnvironmentDiagnosis | null>(null)
  const diagnosing = ref(false)
  // 第一次取数有没有落定：显示闸靠它开，见 useRevealGate。
  const settled = ref(false)

  // 保存脚本与环境变量：一直留在屏幕上的设置区块，结果就地回执（§3.11）。
  const {
    saving,
    saved,
    error: saveError,
    run: runSave,
  } = useSaveState({
    feedback: 'inline',
    messages: {
      saved: t('work.projectSettings.environment.saved'),
      failed: t('work.projectSettings.environment.saveFailed'),
    },
  })
  // 应用（部署一个版本）是一次性动作：结果跟这一次点击走，用 toast 说一声。
  const { saving: applying, run: runApply } = useSaveState({
    feedback: 'toast',
    messages: {
      saved: t('work.projectSettings.environment.applyScheduled'),
      failed: t('work.projectSettings.environment.applyFailed'),
    },
  })

  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let generation = 0

  function stopPolling() {
    clearTimeout(timer)
    timer = undefined
  }

  async function load() {
    const current = ++generation
    error.value = ''
    info.value = null
    selectedRoom.value = null
    try {
      const result = await getProjectEnvironment(projectId())
      if (disposed || current !== generation) return
      info.value = result
      const asked = result.rooms.find((r) => r.id === options.room?.())
      selectedRoom.value = (asked ?? result.rooms.find((r) => r.failure) ?? result.rooms[0])?.id ?? null
    } catch (e) {
      if (current === generation)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.loadFailed')
    } finally {
      settled.value = true
    }
  }

  async function refreshStatus() {
    stopPolling()
    const room = selectedRoom.value
    const current = generation
    if (!room || disposed || !enabled()) return
    try {
      const result = await getRoomEnvironment(projectId(), room)
      if (disposed || current !== generation || room !== selectedRoom.value) return
      status.value = result
    } catch (e) {
      if (current === generation && room === selectedRoom.value)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.statusFailed')
    } finally {
      // 还在这一栏、还是同一个频道，才接着等下一次（离开这一栏就停）。
      if (!disposed && enabled() && current === generation && room === selectedRoom.value)
        timer = setTimeout(refreshStatus, 5000)
    }
  }

  async function save(draft: EnvironmentConfigDraft) {
    await runSave(async () => {
      const values: Record<string, string> = Object.create(null)
      for (const row of draft.variables) {
        if (!row.key || Object.hasOwn(values, row.key))
          throw new Error(t('work.projectSettings.environment.varInvalid'))
        values[row.key] = row.value
      }
      const config = await saveProjectEnvironment(projectId(), {
        setup_script: draft.setup_script,
        startup_script: draft.startup_script,
        variables: values,
      })
      if (info.value) info.value.config = config
    })
  }

  async function apply(latest: boolean) {
    const room = selectedRoom.value
    if (!room) return
    await runApply(async () => {
      await applyRoomEnvironment(projectId(), room, latest)
      await refreshStatus()
    })
  }

  async function diagnose() {
    const room = selectedRoom.value
    if (!room) return
    diagnosing.value = true
    error.value = ''
    try {
      const answer = await options.diagnoseRoom(room)
      if (room === selectedRoom.value) diagnosis.value = answer
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.failure.diagnoseFailed')
    } finally {
      diagnosing.value = false
    }
  }

  /** 重试：按保存的那一版重新准备，在等的那几段对话接着处理。 */
  async function retry() {
    await apply(true)
    await load()
  }

  /** 采用芝士的改法：脚本已在组件里换好、连同环境变量一起报上来，写下去，再重试。 */
  async function adopt(draft: EnvironmentConfigDraft) {
    await save(draft)
    if (saveError.value) return
    diagnosis.value = null
    await retry()
  }

  /** 换一个频道看：状态清空、重问一次；上一次的诊断也不再适用。 */
  function selectRoom(room: string | null) {
    selectedRoom.value = room
  }

  function dismissDiagnosis() {
    diagnosis.value = null
  }

  // 首次取数期间占住设置页的显示闸；这一栏没被打开过就不占。
  holdRevealUntil(() => settled.value || !enabled())
  onMounted(() => {
    if (enabled()) void load()
  })
  watch(enabled, (on) => {
    if (on) void load()
    else stopPolling()
  })
  watch(projectId, () => {
    if (enabled()) void load()
  })
  watch(selectedRoom, () => {
    status.value = null
    diagnosis.value = null
    void refreshStatus()
  })
  onBeforeUnmount(() => {
    disposed = true
    stopPolling()
  })

  return {
    info,
    selectedRoom,
    status,
    error,
    saving,
    saved,
    saveError,
    applying,
    diagnosis,
    diagnosing,
    load,
    refreshStatus,
    save,
    apply,
    diagnose,
    retry,
    adopt,
    selectRoom,
    dismissDiagnosis,
  }
}
