<script setup lang="ts">
// 话题页右侧「例行事务」那一格 —— 纯展示：数据从 props 进，动作从事件出。
//
// 取数在 `components/routine/RoutinePanelHost.vue`（`useRoutineList` 接在那里）。为什么
// 这一格自己必须一滴取数都不沾：`components/panels/` 下的每个 SFC 都是「场景」，
// 2026-09-30 起的场景棘轮要求新场景第一天就是 A 档（只吃 props、只抛事件），而 panels/
// 没有页面那种「外壳 + 视图」的豁免。样板是 `PanelDocView`。
//
// `can_manage` 是后端逐条算好的，这一格不自己算：拿不到权限的那两颗按钮（确认启用、
// 删除）在 `RoutineRow` 里根本不出现，也没有一句解释——做不到的事不画出来，比画出来
// 再说「你不行」干净。
import type { Routine, RoutineInput, RoutineRun } from '@/lib/routine'

import RoutineBoard from '../routine/RoutineBoard.vue'
import RoutineFormDialog from '../routine/RoutineFormDialog.vue'

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
    /** 新建 / 修改那张表：开着没有、改的是哪一条（`null` 是新建）、上次存失败说了什么。 */
    formOpen?: boolean
    editing?: Routine | null
    formError?: string
    saving?: boolean
    /** 新建时定死的那个房间：这一格里的规则长在这个房间，表单不画「在哪个房间执行」。 */
    defaultRoom: string
    /** 那个房间叫什么：表单里那行「在「X」这个频道执行」要写名字，不写 id。 */
    defaultRoomName?: string
  }>(),
  {
    runs: () => ({}),
    openId: null,
    busy: '',
    loading: false,
    error: '',
    formOpen: false,
    editing: null,
    formError: '',
    saving: false,
    defaultRoomName: '',
  }
)

const emit = defineEmits<{
  (e: 'reload'): void
  (e: 'start-new'): void
  (e: 'confirm', routine: Routine): void
  (e: 'pause', routine: Routine): void
  (e: 'resume', routine: Routine): void
  (e: 'run-now', routine: Routine): void
  (e: 'edit', routine: Routine): void
  (e: 'delete', routine: Routine): void
  (e: 'toggle-runs', routine: Routine): void
  /** 点了某次执行交回的一份结果文件：打开它。 */
  (e: 'open-file', path: string): void
  /** 表单开着 / 关上。 */
  (e: 'form-open-change', open: boolean): void
  /** 表单存下去。 */
  (e: 'submit', payload: { room: string; body: RoutineInput }): void
}>()
</script>

<template>
  <div class="routine-panel">
    <div class="routine-panel__bar">
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
      <RoutineBoard
        :routines="routines"
        :runs="runs"
        :open-id="openId"
        :busy="busy"
        :loading="loading"
        :error="error"
        @confirm="(r) => emit('confirm', r)"
        @pause="(r) => emit('pause', r)"
        @resume="(r) => emit('resume', r)"
        @run-now="(r) => emit('run-now', r)"
        @edit="(r) => emit('edit', r)"
        @delete="(r) => emit('delete', r)"
        @toggle-runs="(r) => emit('toggle-runs', r)"
        @open-file="(path) => emit('open-file', path)"
      />
    </div>

    <!-- 房间里的规则长在这个房间：表单不画「在哪个房间执行」那一格，省掉一次没有别的
         答案的选择。 -->
    <RoutineFormDialog
      :model-value="formOpen"
      :routine="editing"
      :default-room="defaultRoom"
      :default-room-name="defaultRoomName"
      :saving="saving"
      :error="formError"
      @update:model-value="(open) => emit('form-open-change', open)"
      @save="(p) => emit('submit', p)"
    />
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
