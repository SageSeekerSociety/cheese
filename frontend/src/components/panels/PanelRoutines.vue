<script setup lang="ts">
// The right-hand 「例行事务」 cell: presentation only — data in through props,
// actions out through events. While a compose or a records page fills the cell
// the toolbar above it hides (each page carries its own 「取消」 / back).
//
// 话题页右侧「例行事务」那一格 —— 纯展示：数据从 props 进，动作从事件出。
//
// 取数在 `components/routine/RoutinePanelHost.vue`（`useRoutineList` + 名册 + 路由都接在
// 那里）。为什么这一格自己必须一滴取数都不沾：`components/panels/` 下的每个 SFC 都是
// 「场景」，2026-09-30 起的场景棘轮要求新场景第一天就是 A 档（只吃 props、只抛事件），
// 而 panels/ 没有页面那种「外壳 + 视图」的豁免。样板是 `PanelDocView`。
//
// `can_delete` 是后端逐条算好的，这一格不自己算：删不掉的那一条在 `RoutineRow` 里
// 不画删除，别的动作都在——读得到就改得动。
import type { Routine, RoutineInput, RoutineRecordsView, RoutineRun } from '@/lib/routine'
import type { RoutineTemplate, RoutineTemplateInput } from '@/lib/routineTemplates'

import RoutineBoard from '../routine/RoutineBoard.vue'
import RoutineCompose from '../routine/RoutineCompose.vue'
import RoutineRecords from '../routine/RoutineRecords.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

withDefaults(
  defineProps<{
    routines: Routine[]
    /** 每条规则最近一次读到的执行记录，键是规则 id。 */
    runs?: Record<string, RoutineRun[]>
    /** 展开着执行记录的那一条。 */
    openId?: string | null
    /** 正在做的那件事，`${id}:${action}`。 */
    busy?: string
    loading?: boolean
    /** 读列表或做动作失败的那句话，摆在列表最上面。 */
    error?: string
    /** 「看全部」那一整页此刻的样子；`null` = 没在看记录，这一格画的是这一串规则。 */
    records?: RoutineRecordsView | null
    /** 建规则那两页：开着没有、改的是哪一条（`null` 是新建）、上次存失败说了什么。 */
    formOpen?: boolean
    editing?: Routine | null
    formError?: string
    saving?: boolean
    /** 新建时定死的那个频道：这一格里的规则长在这个频道，新建那一页不画选择框。 */
    defaultRoom: string
    /** 那个频道叫什么：新建那一页的「频道」那一行上写出来给人看一眼。 */
    roomName?: string
    /** 这间房的队友叫什么：新建那一页说到它。 */
    agentName?: string
    /** 这个项目里存下来的模板（「项目里存的」那一段）。预置的几条在 `lib/routineTemplates.ts` 里。 */
    templates?: RoutineTemplate[]
  }>(),
  {
    runs: () => ({}),
    openId: null,
    records: null,
    busy: '',
    loading: false,
    error: '',
    formOpen: false,
    editing: null,
    formError: '',
    saving: false,
    roomName: '',
    agentName: () => t('work.room.defaultAgentName'),
    templates: () => [],
  }
)

const emit = defineEmits<{
  (e: 'reload'): void
  (e: 'start-new'): void
  (e: 'pause', routine: Routine): void
  (e: 'resume', routine: Routine): void
  (e: 'run-now', routine: Routine): void
  (e: 'edit', routine: Routine): void
  (e: 'delete', routine: Routine): void
  (e: 'toggle-runs', routine: Routine): void
  /** 行里那三次看完，要看这一条的整页记录。 */
  (e: 'open-records', routine: Routine): void
  /** 记录那一页：返回、翻到第几页、筛不筛没能执行的。 */
  (e: 'records-back'): void
  (e: 'records-page', page: number): void
  (e: 'records-failed-only', value: boolean): void
  /** 点了某次执行交回的一份结果文件：打开它。 */
  (e: 'open-file', path: string): void
  /** 表单开着 / 关上。 */
  (e: 'form-open-change', open: boolean): void
  /** 表单存下去。 */
  (e: 'submit', payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }): void
  /** 点了「项目里存的」那一条上的「删除」。 */
  (e: 'delete-template', template: RoutineTemplate): void
  /** 模板那页改了一条，按了「保存」：改的就是那一条模板本身。 */
  (e: 'update-template', payload: { id: string; body: RoutineTemplateInput }): void
}>()
</script>

<template>
  <div class="routine-panel">
    <div v-if="!formOpen && !records" class="routine-panel__bar">
      <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" class="tap-target" @click="emit('start-new')">{{
        t('routines.action.new')
      }}</BaseButton>
      <BaseButton
        kind="ghost"
        icon="mdi-refresh"
        size="sm"
        class="tap-target"
        :aria-label="t('routines.refreshAria')"
        :loading="loading"
        @click="emit('reload')"
      />
    </div>

    <div class="routine-panel__body">
      <RoutineCompose
        v-if="formOpen"
        :model-value="formOpen"
        :routine="editing"
        :default-room="defaultRoom"
        :room-name="roomName"
        :agent-name="agentName"
        :templates="templates"
        :saving="saving"
        :error="formError"
        @update:model-value="(open) => emit('form-open-change', open)"
        @save="(p) => emit('submit', p)"
        @update-template="(p) => emit('update-template', p)"
        @delete-template="(tpl) => emit('delete-template', tpl)"
      />
      <RoutineRecords
        v-else-if="records"
        :title="records.title"
        :runs="records.runs"
        :total="records.total"
        :credits-this-month="records.creditsThisMonth"
        :page="records.page"
        :pages="records.pages"
        :failed-only="records.failedOnly"
        :loading="records.loading"
        @back="emit('records-back')"
        @page="(p) => emit('records-page', p)"
        @failed-only="(v) => emit('records-failed-only', v)"
        @open-file="(path) => emit('open-file', path)"
      />
      <RoutineBoard
        v-else
        :routines="routines"
        :runs="runs"
        :open-id="openId"
        :busy="busy"
        :loading="loading"
        :error="error"
        :link-rooms="false"
        @pause="(r) => emit('pause', r)"
        @resume="(r) => emit('resume', r)"
        @run-now="(r) => emit('run-now', r)"
        @edit="(r) => emit('edit', r)"
        @delete="(r) => emit('delete', r)"
        @toggle-runs="(r) => emit('toggle-runs', r)"
        @open-records="(r) => emit('open-records', r)"
        @open-file="(path) => emit('open-file', path)"
      />
    </div>
  </div>
</template>

<style scoped>
.routine-panel {
  display: flex;
  /* 没写 `flex` 的 flex 子项按内容收缩。面板里 `WorkPanel.vue` 的 `.tabbody` 已经替每一格
     写了；这一条留给不经过它挂载的地方（组件目录）。 */
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}
/* 这一格自己的一条小工具栏：新建（这一格里唯一的主操作）和刷新。它比面板页签低
   一级，所以用文字按钮，不用一块实心的条。 */
.routine-panel__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px;
}
.routine-panel__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 12px 16px;
}
</style>
