<script setup lang="ts">
// 例行事务（项目总览）：这个项目里所有房间的规则，一条一行。
//
// 存下来就在跑，没有人再点一次确认。删不删得掉是后端逐条说的（`can_delete` —— 规则
// 主人或项目管理员），那两颗按钮照着它画，这一页不自己算权限。
//
// 这一页只负责几件属于「页面」的事：把项目里有哪些房间查出来（每条规则要写出它长在哪个
// 房间）、把地址里的 `?routine=` 翻成「展开这一条」、把项目里存下来的模板接上（存在
// 后端，见 `useRoutineTemplates`），以及页头上那颗「刷新」。规则那一串和改规则的那一页
// 是 `components/routine/` 里的共用件 —— 房间右侧「例行事务」那一格画的是同一份。
//
// 这一页建不了规则：建只在房间右侧那一格。这里摆着全项目的规则，看得到、改得动。
import type { RoutineInput } from '@/lib/routine'
import type { RoutineTemplateInput } from '@/lib/routineTemplates'
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

const {
  templates,
  add: addTemplate,
  change: changeTemplate,
  remove: removeTemplate,
} = useRoutineTemplates(() => props.projectId)

const {
  routines,
  runs,
  openId,
  records,
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
  closeForm,
  submit,
  openRecords,
  closeRecords,
  goRecordsPage,
  setRecordsFailedOnly,
} = useRoutineList({ projectId: props.projectId })

const roomNames = computed(() => Object.fromEntries(rooms.value.map((r) => [r.id, r.title])))
// 改一条已有的时，表单里「频道」那一行写的是它长在哪个房间：这一页建不了规则，这里
// 没有可挑的频道。
const editingRoomName = computed(() => (editing.value ? roomNames.value[editing.value.topic_id] ?? '' : ''))

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
 *  不然项目里会出现一条从没跑起来的表。模板和规则是两件事，两处各自会失败：规则
 *  存下了、模板没存上要说一句，那一条不会自己冒出来，人下次也找不到它。 */
async function onSubmit(payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }) {
  const saved = await submit(payload)
  if (!saved || !payload.keepAsTemplate) return
  if (!(await addTemplate(payload.body))) error.value = t('routines.template.saveFailed')
}

/** 模板那页改了一条模板：和存一条规则一样，改上了把这一页收起来（列表里那一条已经
 *  是新的了）；没改上就留在这张表上写一句，人再存一次不用重填。 */
async function onUpdateTemplate(payload: { id: string; body: RoutineTemplateInput }) {
  saving.value = true
  formError.value = ''
  try {
    if (await changeTemplate(payload.id, payload.body)) formOpen.value = false
    else formError.value = t('routines.template.saveFailed')
  } finally {
    saving.value = false
  }
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
])
</script>

<template>
  <ProjectRoutinesViewView
    :agent-name="agentName"
    :routines="routines"
    :runs="runs"
    :open-id="openId"
    :records="records"
    :busy="busy"
    :loading="loading"
    :error="error"
    :room-names="roomNames"
    :room-name="editingRoomName"
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
    @open-records="openRecords"
    @records-back="closeRecords"
    @records-page="goRecordsPage"
    @records-failed-only="setRecordsFailedOnly"
    @update:model-value="(open) => (open ? (formOpen = true) : closeForm())"
    @save="onSubmit"
    @update-template="(payload) => void onUpdateTemplate(payload)"
    @delete-template="(tpl) => void removeTemplate(tpl.id)"
  />
</template>
