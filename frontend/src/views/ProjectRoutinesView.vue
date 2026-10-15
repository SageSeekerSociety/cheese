<script setup lang="ts">
// 例行事务（项目总览）：这个项目里所有房间的规则，一条一行。
//
// 存下来就在跑，没有人再点一次确认。删不删得掉是后端逐条说的（`can_delete` —— 规则
// 主人或项目管理员），那两颗按钮照着它画，这一页不自己算权限。
//
// 这一页只负责几件属于「页面」的事：把项目里有哪些房间查出来（新建时要选一个）、把
// 地址里的 `?routine=` 翻成「展开这一条」、把「我存的」模板接上（存在这台浏览器上，
// 见 `useRoutineTemplates`），以及页头上那两颗命令。规则那一串和建规则那两页是
// `components/routine/` 里的共用件 —— 房间右侧「例行事务」那一格画的是同一份。
import type { RoutineInput } from '@/lib/routine'
import type { Topic } from '../cx_types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useProjectAgentName } from '@/composables/useProjectAgentName'
import { useRoutineList } from '@/composables/useRoutineList'
import { useRoutineTemplates } from '@/composables/useRoutineTemplates'

import { listTopics } from '../api'

import ProjectRoutinesViewView from './ProjectRoutinesViewView.vue'

import { useCommands } from '@/commands'
import { t } from '@/i18n'
import { focusRow } from '@/lib/focusRow'

const props = defineProps<{ projectId: string }>()
const route = useRoute()

const agentName = useProjectAgentName()

const rooms = ref<Topic[]>([])

const { templates, add: addTemplate, remove: removeTemplate } = useRoutineTemplates(() => props.projectId)

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

/** 表单存下去。勾了「存成模板」就把这张表也收成一条 —— 存规则失败时那一条不留下，
 *  不然「我存的」里会出现一条从没跑起来的表。 */
async function onSubmit(payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }) {
  const saved = await submit(payload)
  if (saved && payload.keepAsTemplate) addTemplate(payload.body)
}

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
    :agent-name="agentName"
    :routines="routines"
    :runs="runs"
    :open-id="openId"
    :busy="busy"
    :loading="loading"
    :error="error"
    :room-names="roomNames"
    :rooms="rooms"
    :default-room="defaultRoom"
    :form-open="formOpen"
    :editing="editing"
    :saving="saving"
    :form-error="formError"
    :templates="templates"
    @pause="(r) => act(r, 'pause')"
    @resume="(r) => act(r, 'resume')"
    @run-now="(r) => act(r, 'run-now')"
    @edit="startEdit"
    @delete="remove"
    @toggle-runs="(r) => toggleRuns(r)"
    @update:model-value="(open) => (open ? (formOpen = true) : closeForm())"
    @save="onSubmit"
    @delete-template="(tpl) => removeTemplate(tpl.id)"
  />
</template>
