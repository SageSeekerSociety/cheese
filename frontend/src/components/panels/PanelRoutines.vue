<script setup lang="ts">
// 话题页右侧「定时与触发」那一格 —— 一只薄容器。
//
// 它要回答的问题只有一句：「这个房间的规则有哪些，此刻我能对它们做什么」。项目「定时
// 与触发」页问的是同一句话，只是范围是整个项目，所以两边画的是同一串（`components/
// routine/RoutineBoard.vue`）、填的是同一张表（`RoutineFormDialog.vue`）、取数走的是
// 同一个组合式函数（`composables/useRoutineList.ts`，`topicId` 一给就只看这一个房间）。
// 这一只只负责把那两边接起来：范围是这一个房间、新建的房间是定死的、结果的去处就在
// 原地（不画「去房间」的链接）。
//
// `can_manage` 是后端逐条算好的，这一格不自己算：拿不到权限的那些规则在 `RoutineRow`
// 里不画按钮、只写一句「这条规则由谁管」——只读不是「偷偷禁用」，是说出来。
//
// 取数放在组合式函数里而不是这里，和 PanelPreview / PanelChanges 同一个理由：让这一只
// 基本不再长，也让 `/demo` 那条假后端能照旧喂它。
import { useRoutineList } from '../../composables/useRoutineList'
import RoutineBoard from '../routine/RoutineBoard.vue'
import RoutineFormDialog from '../routine/RoutineFormDialog.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    // 一轮结束由工作面板加一：芝士可能刚在房间里起草了一条规则，名单要跟上。
    refreshTick?: number
  }>(),
  { refreshTick: 0 }
)

const {
  routines,
  runs,
  openId,
  busy,
  loading,
  error,
  formOpen,
  editing,
  formError,
  saving,
  reload,
  toggleRuns,
  act,
  remove,
  startEdit,
  startNew,
  closeForm,
  submit,
} = useRoutineList({
  // 工作面板只在选中了话题之后才画这一格，所以这两个值此刻是有的；组合式函数对
  // 空项目名也守得住（`reload` 直接不请求），话题一到那次 watch 会重来。
  projectId: props.projectId ?? '',
  topicId: props.topicId,
  tick: props.refreshTick,
})
</script>

<template>
  <div class="routine-panel">
    <div class="routine-panel__bar">
      <v-btn size="small" variant="text" prepend-icon="mdi-plus" class="tap-target" @click="startNew">新建</v-btn>
      <v-btn
        icon="mdi-refresh"
        size="small"
        variant="text"
        color="on-surface-variant"
        class="tap-target"
        aria-label="刷新规则"
        :loading="loading"
        @click="reload"
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
        :link-rooms="false"
        @confirm="(r) => act(r, 'confirm')"
        @pause="(r) => act(r, 'pause')"
        @resume="(r) => act(r, 'resume')"
        @run-now="(r) => act(r, 'run-now')"
        @edit="startEdit"
        @delete="remove"
        @toggle-runs="(r) => toggleRuns(r)"
      />
    </div>

    <!-- 房间里的规则长在这个房间：表单不画「在哪个房间执行」那一格，省掉一次没有别的
         答案的选择。 -->
    <RoutineFormDialog
      :model-value="formOpen"
      :routine="editing"
      :default-room="topicId ?? ''"
      :saving="saving"
      :error="formError"
      @update:model-value="(open) => (open ? (formOpen = true) : closeForm())"
      @save="submit"
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
