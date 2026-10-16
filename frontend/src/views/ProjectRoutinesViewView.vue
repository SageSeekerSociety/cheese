<script setup lang="ts">
// 例行事务的画面：一条规则一行（`RoutineBoard`），建一条规则的那两页
// （`RoutineCompose`）。两样都是 `components/routine/` 里的共用件 —— 房间右侧那一格
// 画的是同一份；取数、读地址留在容器 `ProjectRoutinesView.vue` 里，这一半只收 props、
// 只把动作抛上去。
import type { Topic } from '@/cx_types'
import type { Routine, RoutineInput, RoutineRecordsView, RoutineRun } from '@/lib/routine'
import type { RoutineTemplate } from '@/lib/routineTemplates'

import AppPage from '@/components/common/AppPage.vue'
import RoutineBoard from '@/components/routine/RoutineBoard.vue'
import RoutineCompose from '@/components/routine/RoutineCompose.vue'
import RoutineRecords from '@/components/routine/RoutineRecords.vue'
import { t } from '@/i18n'

defineProps<{
  /** 这个项目里队友的名字：导语里要用它点名（「…」由容器查名册后传下来）。 */
  agentName: string
  routines: Routine[]
  runs: Record<string, RoutineRun[]>
  openId: string | null
  /** 「看全部」那一整页此刻的样子；`null` = 没在看记录，这一页画的是这一串规则。 */
  records: RoutineRecordsView | null
  busy: string
  loading: boolean
  error: string
  roomNames: Record<string, string>
  rooms: Topic[]
  defaultRoom: string
  formOpen: boolean
  editing: Routine | null
  saving: boolean
  formError: string
  /** 这个项目里存下来的模板（「项目里存的」那一段）。预置在 `lib/routineTemplates.ts` 里。 */
  templates: RoutineTemplate[]
}>()

defineEmits<{
  pause: [routine: Routine]
  resume: [routine: Routine]
  'run-now': [routine: Routine]
  edit: [routine: Routine]
  delete: [routine: Routine]
  'toggle-runs': [routine: Routine]
  'open-records': [routine: Routine]
  'records-back': []
  'records-page': [page: number]
  'records-failed-only': [value: boolean]
  'update:model-value': [open: boolean]
  save: [payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }]
  'delete-template': [template: RoutineTemplate]
}>()
</script>

<template>
  <AppPage :title="t('navigation.project.routines')">
    <!-- 建一条规则的那两页占满这一页：工具条上的「新建」此刻没有别的意思，那一串也
         收起来（这一页自己带着「取消」）。 -->
    <RoutineCompose
      v-if="formOpen"
      :model-value="formOpen"
      :routine="editing"
      :rooms="rooms"
      :default-room="defaultRoom"
      :templates="templates"
      :saving="saving"
      :error="formError"
      @update:model-value="(open) => $emit('update:model-value', open)"
      @save="(payload) => $emit('save', payload)"
      @delete-template="(tpl) => $emit('delete-template', tpl)"
    />

    <!-- 看记录的时候整页只剩记录：抬头写着是哪一条的，返回箭头带回这一串。 -->
    <RoutineRecords
      v-else-if="records"
      :title="records.title"
      :runs="records.runs"
      :total="records.total"
      :page="records.page"
      :pages="records.pages"
      :failed-only="records.failedOnly"
      :loading="records.loading"
      :room-to="records.roomTo"
      @back="$emit('records-back')"
      @page="(p) => $emit('records-page', p)"
      @failed-only="(v) => $emit('records-failed-only', v)"
    />

    <div v-else>
      <p class="t-body c-muted mb-6">{{ t('routines.intro', { name: agentName }) }}</p>

      <RoutineBoard
        :routines="routines"
        :runs="runs"
        :open-id="openId"
        :busy="busy"
        :loading="loading"
        :error="error"
        :room-names="roomNames"
        @pause="(r) => $emit('pause', r)"
        @resume="(r) => $emit('resume', r)"
        @run-now="(r) => $emit('run-now', r)"
        @edit="(r) => $emit('edit', r)"
        @delete="(r) => $emit('delete', r)"
        @toggle-runs="(r) => $emit('toggle-runs', r)"
        @open-records="(r) => $emit('open-records', r)"
      />
    </div>
  </AppPage>
</template>
