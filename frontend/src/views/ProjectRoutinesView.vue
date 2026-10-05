<script setup lang="ts">
// 定时与触发（项目总览）：这个项目里所有房间的规则，一条一行。
//
// 芝士起草的规则停在「待确认」，只有人点「确认启用」才会开始跑：无人值守地动手，得先
// 有人读过它要做什么、用哪些资料、结果放哪。一条规则归谁管是后端逐条说的（`can_manage`
// ——规则主人或项目管理员），按钮照着它画，这一页不自己算权限。
//
// 这一页只负责三件属于「页面」的事：把项目里有哪些房间查出来（新建时要选一个）、把
// 地址里的 `?routine=` 翻成「展开这一条」，以及页头上那两颗命令。规则本身那一串和那张
// 表单是 `components/routine/` 里的共用件 —— 房间右侧「定时与触发」那一格画的是同一份。
import type { UserRefTarget } from '@/lib/userRef'
import type { Topic } from '../cx_types'

import { computed, getCurrentInstance, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useRoutineList } from '@/composables/useRoutineList'

import { listTopics } from '../api'

import ProjectRoutinesViewView from './ProjectRoutinesViewView.vue'

import { useCommands } from '@/commands'
import { t } from '@/i18n'
import { focusRow } from '@/lib/focusRow'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const route = useRoute()

// 路由和 pinia 都从 app 上拿（和 composables/useUserRef 同一个理由）：这一页在单测里
// 是孤立渲染的，那里没有路由、没有 store，照样画，只是人名不可点、画 handle。
const app = getCurrentInstance()?.appContext.config.globalProperties
const workspace = app?.$pinia ? useWorkspaceStore() : null

/** 人名 → 显示名：行里的人名只画字（`UserRef`），名册在这里查一次。 */
const userNames = computed(() =>
  Object.fromEntries(
    (workspace?.members ?? []).map((m) => [m.user_handle, m.name]).filter((e): e is [string, string] => Boolean(e[1]))
  )
)

function go(target: UserRefTarget) {
  void app?.$router?.push(target)
}

const rooms = ref<Topic[]>([])

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
} = useRoutineList({ projectId: props.projectId })

// 新建时落在哪个房间：地址里点名了就用它（房间面板里的「新建」也是这么带过来的），
// 否则第一个房间。
const defaultRoom = computed(() => (typeof route.query.room === 'string' ? route.query.room : rooms.value[0]?.id ?? ''))
const roomNames = computed(() => Object.fromEntries(rooms.value.map((r) => [r.id, r.title])))

async function loadRooms() {
  const projectId = props.projectId
  try {
    const topics = await listTopics(projectId)
    if (props.projectId !== projectId) return
    rooms.value = topics.data.filter((tp) => tp.status !== 'archived')
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('routines.roomsFailed')
  }
}

// 从一条平台通知点进来（`?routine=<id>`）：那一条自己展开，滚到它跟前。
watch(
  routines,
  (rows) => {
    const focus = typeof route.query.routine === 'string' ? route.query.routine : null
    if (!focus) return
    const row = rows.find((r) => r.id === focus)
    if (!row) return
    void toggleRuns(row, true)
    void focusRow(`[data-routine="${CSS.escape(focus)}"]`)
  },
  { immediate: true }
)

// 这一页归这个项目。项目和房间各自去查：房间是这一页要用的（选一个），规则那一串在
// `useRoutineList` 里跟着 projectId 自己重读。
watch(
  () => props.projectId,
  () => {
    rooms.value = []
    void loadRooms()
  },
  { immediate: true }
)

useCommands(() => [
  {
    id: 'routines.refresh',
    title: t('routines.action.refresh'),
    palette: false,
    icon: 'mdi-refresh',
    loading: loading.value,
    header: {},
    run: reload,
  },
  {
    id: 'routines.new',
    title: t('routines.action.new'),
    icon: 'mdi-plus',
    disabled: !rooms.value.length,
    header: { primary: true, accent: true },
    run: startNew,
  },
])
</script>

<template>
  <ProjectRoutinesViewView
    :routines="routines"
    :runs="runs"
    :open-id="openId"
    :busy="busy"
    :loading="loading"
    :error="error"
    :room-names="roomNames"
    :user-names="userNames"
    :rooms="rooms"
    :default-room="defaultRoom"
    :form-open="formOpen"
    :editing="editing"
    :saving="saving"
    :form-error="formError"
    @navigate="go"
    @confirm="(r) => act(r, 'confirm')"
    @pause="(r) => act(r, 'pause')"
    @resume="(r) => act(r, 'resume')"
    @run-now="(r) => act(r, 'run-now')"
    @edit="startEdit"
    @delete="remove"
    @toggle-runs="(r) => toggleRuns(r)"
    @update:model-value="(open) => (open ? (formOpen = true) : closeForm())"
    @save="submit"
  />
</template>
