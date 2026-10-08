<script setup lang="ts">
// 房间面板「定时与触发」那一格的取数外壳。
//
// 它要回答的问题只有一句：「这个房间的规则有哪些，此刻我能对它们做什么」。项目「定时
// 与触发」页问的是同一句话，只是范围是整个项目，所以两边画的是同一串（`RoutineBoard`，
// 经 `panels/PanelRoutines.vue`）、填的是同一张表（`RoutineFormDialog`）、取数走的是
// 同一个组合式函数（`composables/useRoutineList.ts`，`topicId` 一给就只看这一个房间）。
// 这一只负责把那两边接起来：范围是这一个房间、新建的房间是定死的、结果的去处就在
// 原地（不画「去房间」的链接）。
//
// 名册和路由也接在这里（人名的显示名、点了去哪），画的那一半只收结果——panels/ 下的
// 每个 SFC 都是场景棘轮里的「场景」，新场景必须第一天就是 A 档，所以取数一滴都不能
// 漏到 `PanelRoutines.vue` 里去。
import type { UserRefTarget } from '@/lib/userRef'

import { computed, getCurrentInstance } from 'vue'

import { useNavigation } from '@/composables/useNavigation'
import { useRoutineList } from '@/composables/useRoutineList'

import PanelRoutines from '../panels/PanelRoutines.vue'

import { useWorkspaceStore } from '@/stores/workspace'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    // 一轮结束由工作面板加一：芝士可能刚在房间里起草了一条规则，名单要跟上。
    refreshTick?: number
  }>(),
  { refreshTick: 0 }
)

// 路由走 useNavigation()、pinia 从 app 上拿（和 composables/useUserRef 同一个理由）：
// 这一只也可能在没装路由、没装 store 的树里被孤立渲染（单测），那里照样画，只是人名
// 不可点、画 handle。
const nav = useNavigation()
const app = getCurrentInstance()?.appContext.config.globalProperties
const store = app?.$pinia ? useWorkspaceStore() : null

/** 人名 → 显示名：原来每颗 UserRefLink 各自查同一个 store，现在在这里查一次。 */
const userNames = computed(() =>
  Object.fromEntries(
    (store?.members ?? []).map((m) => [m.user_handle, m.name]).filter((e): e is [string, string] => Boolean(e[1]))
  )
)

function go(target: UserRefTarget) {
  void nav?.navigate(target)
}

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
    :user-names="userNames"
    @reload="reload"
    @start-new="startNew"
    @confirm="(r) => act(r, 'confirm')"
    @pause="(r) => act(r, 'pause')"
    @resume="(r) => act(r, 'resume')"
    @run-now="(r) => act(r, 'run-now')"
    @edit="startEdit"
    @delete="remove"
    @toggle-runs="(r) => toggleRuns(r)"
    @form-open-change="(open) => (open ? (formOpen = true) : closeForm())"
    @submit="submit"
    @navigate="go"
  />
</template>
