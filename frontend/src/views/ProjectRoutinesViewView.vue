<script setup lang="ts">
// 定时与触发的画面：一条规则一行（`RoutineBoard`），加上新建 / 修改那张表单
// （`RoutineFormDialog`）。规则本身和表单都是 `components/routine/` 里的共用件；
// 取数、读地址、权限全留在容器 `ProjectRoutinesView.vue` 里 —— 这一半只收 props、
// 只把动作抛上去。
import type { Topic } from '@/cx_types'
import type { Routine, RoutineInput, RoutineRun } from '@/lib/routine'
import type { UserRefTarget } from '@/lib/userRef'

import AppPage from '@/components/common/AppPage.vue'
import RoutineBoard from '@/components/routine/RoutineBoard.vue'
import RoutineFormDialog from '@/components/routine/RoutineFormDialog.vue'
import { t } from '@/i18n'

defineProps<{
  routines: Routine[]
  runs: Record<string, RoutineRun[]>
  openId: string | null
  busy: string
  loading: boolean
  error: string
  roomNames: Record<string, string>
  userNames: Record<string, string>
  rooms: Topic[]
  defaultRoom: string
  formOpen: boolean
  editing: Routine | null
  saving: boolean
  formError: string
}>()

defineEmits<{
  navigate: [target: UserRefTarget]
  confirm: [routine: Routine]
  pause: [routine: Routine]
  resume: [routine: Routine]
  'run-now': [routine: Routine]
  edit: [routine: Routine]
  delete: [routine: Routine]
  'toggle-runs': [routine: Routine]
  'update:model-value': [open: boolean]
  save: [payload: { room: string; body: RoutineInput }]
}>()
</script>

<template>
  <AppPage :title="t('navigation.project.routines')">
    <div>
      <p class="t-body c-muted mb-6">{{ t('routines.intro') }}</p>

      <RoutineBoard
        :routines="routines"
        :runs="runs"
        :open-id="openId"
        :busy="busy"
        :loading="loading"
        :error="error"
        :room-names="roomNames"
        :user-names="userNames"
        @navigate="(target) => $emit('navigate', target)"
        @confirm="(r) => $emit('confirm', r)"
        @pause="(r) => $emit('pause', r)"
        @resume="(r) => $emit('resume', r)"
        @run-now="(r) => $emit('run-now', r)"
        @edit="(r) => $emit('edit', r)"
        @delete="(r) => $emit('delete', r)"
        @toggle-runs="(r) => $emit('toggle-runs', r)"
      />
    </div>

    <RoutineFormDialog
      :model-value="formOpen"
      :routine="editing"
      :rooms="rooms"
      :default-room="defaultRoom"
      :saving="saving"
      :error="formError"
      @update:model-value="(open) => $emit('update:model-value', open)"
      @save="(payload) => $emit('save', payload)"
    />
  </AppPage>
</template>
