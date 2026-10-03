<script setup lang="ts">
// 话题页右侧「定时与触发」那一格 —— 纯展示：数据从 props 进，动作从事件出。
//
// 取数在 `components/routine/RoutinePanelHost.vue`（`useRoutineList` + 名册 + 路由都接在
// 那里）。为什么这一格自己必须一滴取数都不沾：`components/panels/` 下的每个 SFC 都是
// 「场景」，2026-09-30 起的场景棘轮要求新场景第一天就是 A 档（只吃 props、只抛事件），
// 而 panels/ 没有页面那种「外壳 + 视图」的豁免。样板是 `PanelDocView`。
//
// `can_manage` 是后端逐条算好的，这一格不自己算：拿不到权限的那些规则在 `RoutineRow`
// 里不画按钮、只写一句「这条规则由谁管」——只读不是「偷偷禁用」，是说出来。
import type { Routine, RoutineInput, RoutineRun } from '@/lib/routine'
import type { UserRefTarget } from '@/lib/userRef'

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
    /** 人名 → 显示名。名册查询在外壳里，这一格只收结果。 */
    userNames?: Record<string, string>
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
    userNames: () => ({}),
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
  /** 表单开着 / 关上。 */
  (e: 'form-open-change', open: boolean): void
  /** 表单存下去。 */
  (e: 'submit', payload: { room: string; body: RoutineInput }): void
  /** 点了行里的某个人名：去他的成员页 / 主页。 */
  (e: 'navigate', target: UserRefTarget): void
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
        :user-names="userNames"
        :link-rooms="false"
        @confirm="(r) => emit('confirm', r)"
        @pause="(r) => emit('pause', r)"
        @resume="(r) => emit('resume', r)"
        @run-now="(r) => emit('run-now', r)"
        @edit="(r) => emit('edit', r)"
        @delete="(r) => emit('delete', r)"
        @toggle-runs="(r) => emit('toggle-runs', r)"
        @navigate="(t) => emit('navigate', t)"
      />
    </div>

    <!-- 房间里的规则长在这个房间：表单不画「在哪个房间执行」那一格，省掉一次没有别的
         答案的选择。 -->
    <RoutineFormDialog
      :model-value="formOpen"
      :routine="editing"
      :default-room="defaultRoom"
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
