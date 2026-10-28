<script setup lang="ts">
// 例行事务的画面：一条规则一行（`RoutineBoard`），改一条已有的那一页
// （`RoutineCompose`）。两样都是 `components/routine/` 里的共用件 —— 房间右侧那一格
// 画的是同一份；取数、读地址留在容器 `ProjectRoutinesView.vue` 里，这一半只收 props、
// 只把动作抛上去。改一条已有的、或者看记录的时候，整页只剩那一件：工具条上那串
// （新建、刷新）此刻没有别的意思，收起来（那两页各自带着「取消」或返回箭头）。
import type { Routine, RoutineInput, RoutineRecordsView, RoutineRun } from '@/lib/routine'
import type { RoutineTemplate, RoutineTemplateInput } from '@/lib/routineTemplates'

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
  /** 改一条已有的时，表单里「频道」那一行写的名字。这一页建不了规则，没有可挑的频道。 */
  roomName: string
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
  'update-template': [payload: { id: string; body: RoutineTemplateInput }]
}>()
</script>

<template>
  <AppPage :title="t('navigation.project.routines')">
    <RoutineCompose
      v-if="formOpen"
      :model-value="formOpen"
      :routine="editing"
      :room-name="roomName"
      :agent-name="agentName"
      :templates="templates"
      :saving="saving"
      :error="formError"
      @update:model-value="(open) => $emit('update:model-value', open)"
      @save="(payload) => $emit('save', payload)"
      @update-template="(payload) => $emit('update-template', payload)"
      @delete-template="(tpl) => $emit('delete-template', tpl)"
    />

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
