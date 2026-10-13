<script setup lang="ts">
// 房间面板「例行事务」那一格的取数外壳。
//
// 它要回答的问题只有一句：「这个房间的规则有哪些，此刻我能对它们做什么」。项目「例行
// 事务」页问的是同一句话，只是范围是整个项目，所以两边画的是同一串（`RoutineBoard`，
// 经 `panels/PanelRoutines.vue`）、填的是同一张表（`RoutineFormDialog`）、取数走的是
// 同一个组合式函数（`composables/useRoutineList.ts`，`topicId` 一给就只看这一个房间）。
// 这一只负责把那两边接起来：范围是这一个房间、新建的房间是定死的、结果文件就在这个
// 房间的文件区里（不画「去房间」的链接 —— 那一格就在这个房间里，点一份就当场打开它）。
//
// 画的那一半只收结果——panels/ 下的每个 SFC 都是场景棘轮里的「场景」，新场景必须第一天
// 就是 A 档，所以取数一滴都不能漏到 `PanelRoutines.vue` 里去。
import { useRoutineList } from '@/composables/useRoutineList'

import PanelRoutines from '../panels/PanelRoutines.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    /** 这个房间叫什么。表单里那行「在「X」这个频道执行」写的是它。 */
    topicTitle?: string | null
    projectId: string | null
    // 一轮结束由工作面板加一：芝士可能刚在房间里起草了一条规则，名单要跟上。
    refreshTick?: number
  }>(),
  { refreshTick: 0 }
)

const emit = defineEmits<{
  /** 点了某次执行交回的一份结果文件：工作面板把它开成一格。 */
  (e: 'open-file', path: string): void
}>()

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
  <PanelRoutines
    :routines="routines"
    :runs="runs"
    :open-id="openId"
    :busy="busy"
    :loading="loading"
    :error="error"
    :form-open="formOpen"
    :editing="editing"
    :form-error="formError"
    :saving="saving"
    :default-room="topicId ?? ''"
    :default-room-name="topicTitle ?? ''"
    @reload="reload"
    @start-new="startNew"
    @confirm="(r) => act(r, 'confirm')"
    @pause="(r) => act(r, 'pause')"
    @resume="(r) => act(r, 'resume')"
    @run-now="(r) => act(r, 'run-now')"
    @edit="startEdit"
    @delete="remove"
    @toggle-runs="(r) => toggleRuns(r)"
    @open-file="(path) => emit('open-file', path)"
    @form-open-change="(open) => (open ? (formOpen = true) : closeForm())"
    @submit="submit"
  />
</template>
