// 项目设置页「运行环境」这一节的数据：脚本与环境变量、选中的那个频道、它的准备
// 状态（轮询来的）、以及这个频道最近一次失败和「让芝士看看」的结果。
//
// 拆自 `components/ProjectEnvironmentSettings.vue`：取数、保存、轮询、定时器都在
// 这一层，组件只画。这一节比别的区块绕，三件事在这里看着：
//
//   1. **取数分代**。换项目、或者在等的时候又切了频道，之前那次请求回来时不许写
//      状态 —— `generation` 加一，回来对不上就丢掉。卸载之后同样不许写。
//   2. **状态是轮询来的**，每 5 秒问一次当前频道；切频道要立刻清掉上一份、马上
//      再问一次，定时器跟着那一次的结果续上。卸载要停表。
//   3. **两处回执不一样**：脚本和环境变量是留在屏幕上的设置区块，结果就地报
//      （`inline`，见 useSaveState）；「应用」是一次性动作，结果跟这一次点击走
//      （`toast`）。
//
// 频道最近那次失败要先问一次（`diagnoseRoom`），那是页面的动作，这里只负责把
// 结果落在当前选中的频道上 —— 问的时候人在别处切走了，答案就不要了。
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '@/cx_types'
import type { EnvironmentDiagnosis } from '@/types/environment'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import { applyRoomEnvironment, getProjectEnvironment, getRoomEnvironment, saveProjectEnvironment } from '@/api'
import { t } from '@/i18n'

export interface ProjectEnvironmentOptions {
  projectId: () => string
  /** 从一条「环境准备失败」点过来的：先看那个频道。 */
  room: () => string | undefined
  /** 「让芝士看看」：问一次这个频道最近那次失败，由页面去问。 */
  diagnoseRoom: (roomId: string) => Promise<EnvironmentDiagnosis>
}

export function useProjectEnvironment(options: ProjectEnvironmentOptions) {
  const info = ref<ProjectEnvironmentInfo | null>(null)
  const setup = ref('')
  const startup = ref('')
  const variables = ref<{ key: string; value: string }[]>([])
  const selectedRoom = ref<string | null>(null)
  const status = ref<EnvironmentStatus | null>(null)
  const error = ref('')

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

  async function load() {
    const current = ++generation
    error.value = ''
    info.value = null
    selectedRoom.value = null
    try {
      const result = await getProjectEnvironment(options.projectId())
      if (disposed || current !== generation) return
      info.value = result
      setup.value = result.config.setup_script
      startup.value = result.config.startup_script
      variables.value = Object.entries(result.config.variables).map(([key, value]) => ({ key, value }))
      const asked = result.rooms.find((r) => r.id === options.room())
      selectedRoom.value = (asked ?? result.rooms.find((r) => r.failure) ?? result.rooms[0])?.id ?? null
    } catch (e) {
      if (current === generation)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.loadFailed')
    }
  }

  async function refreshStatus() {
    clearTimeout(timer)
    const room = selectedRoom.value
    const current = generation
    if (!room || disposed) return
    try {
      const result = await getRoomEnvironment(options.projectId(), room)
      if (disposed || current !== generation || room !== selectedRoom.value) return
      status.value = result
    } catch (e) {
      if (current === generation && room === selectedRoom.value)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.statusFailed')
    } finally {
      if (!disposed && current === generation && room === selectedRoom.value) timer = setTimeout(refreshStatus, 5000)
    }
  }

  async function save() {
    await runSave(async () => {
      const values: Record<string, string> = Object.create(null)
      for (const row of variables.value) {
        if (!row.key || Object.hasOwn(values, row.key))
          throw new Error(t('work.projectSettings.environment.varInvalid'))
        values[row.key] = row.value
      }
      const config = await saveProjectEnvironment(options.projectId(), {
        setup_script: setup.value,
        startup_script: startup.value,
        variables: values,
      })
      if (info.value) info.value.config = config
    })
  }

  async function apply(latest: boolean) {
    const room = selectedRoom.value
    if (!room) return
    await runApply(async () => {
      await applyRoomEnvironment(options.projectId(), room, latest)
      await refreshStatus()
    })
  }

  // ---- 这个频道最近一次失败：让芝士看看、重试、采用它的改法 ----
  const failing = computed(() => info.value?.rooms.find((r) => r.id === selectedRoom.value && r.failure) ?? null)
  const diagnosis = ref<EnvironmentDiagnosis | null>(null)
  const diagnosing = ref(false)
  watch(selectedRoom, () => (diagnosis.value = null))

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

  /** 采用芝士的改法：写进脚本，保存，再重试。 */
  async function adopt() {
    const answer = diagnosis.value
    if (!answer) return
    if (answer.setup_script !== null) setup.value = answer.setup_script
    if (answer.startup_script !== null) startup.value = answer.startup_script
    await save()
    if (saveError.value) return
    diagnosis.value = null
    await retry()
  }

  watch(selectedRoom, () => {
    status.value = null
    void refreshStatus()
  })
  onBeforeUnmount(() => {
    disposed = true
    clearTimeout(timer)
  })

  return {
    info,
    setup,
    startup,
    variables,
    selectedRoom,
    status,
    error,
    saving,
    saved,
    saveError,
    applying,
    failing,
    diagnosis,
    diagnosing,
    load,
    refreshStatus,
    save,
    apply,
    diagnose,
    retry,
    adopt,
  }
}
