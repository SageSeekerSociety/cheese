<script setup lang="ts">
// 房间面板「例行事务」那一格的取数外壳。
//
// 它要回答的问题只有一句：「这个房间的规则有哪些，此刻我能对它们做什么」。项目「定时
// 与触发」页问的是同一句话，只是范围是整个项目，所以两边画的是同一串（`RoutineBoard`，
// 经 `panels/PanelRoutines.vue`）、建规则用的是同两页（`RoutineCompose`）、取数走的是
// 同一个组合式函数（`composables/useRoutineList.ts`，`topicId` 一给就只看这一个房间）。
// 这一只负责把那两边接起来：范围是这一个房间、新建的房间是定死的、结果文件就在这个
// 房间的文件区里（不画「去房间」的链接 —— 那一格就在这个房间里，点一份就当场打开它）。
//
// 取数都接在这里，画的那一半只收结果——panels/ 下的每个 SFC 都是场景棘轮里的「场景」，
// 新场景必须第一天就是 A 档，所以取数一滴都不能漏到 `PanelRoutines.vue` 里去。
import type { Routine, RoutineInput } from '@/lib/routine'
import type { RoutineTemplateInput } from '@/lib/routineTemplates'

import { nextTick, ref, watch } from 'vue'

import { useRoutineList } from '@/composables/useRoutineList'
import { useRoutineTemplates } from '@/composables/useRoutineTemplates'

import PanelRoutines from '../panels/PanelRoutines.vue'

import { t } from '@/i18n'
import { focusRow } from '@/lib/focusRow'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    /** 这个房间叫什么：新建那一页的「频道」那一行上写出来。 */
    roomTitle?: string
    /** 这间房的队友叫什么，见 PanelRoutines。 */
    agentName?: string
    // 一轮结束由工作面板加一：芝士可能刚在房间里起草了一条规则，名单要跟上。
    refreshTick?: number
    /** 一句提到这条规则的话里，那名字的链接（`?routine=`）指着的那一条。 */
    routineId?: string | null
  }>(),
  { roomTitle: '', refreshTick: 0, routineId: null, agentName: undefined }
)

const emit = defineEmits<{
  /** 点了某次执行交回的一份结果文件：工作面板把它开成一格。 */
  (e: 'open-file', path: string): void
}>()

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
  startNew,
  closeForm,
  submit,
  openRecords,
  closeRecords,
  goRecordsPage,
  setRecordsFailedOnly,
} = useRoutineList({
  // 工作面板只在选中了话题之后才画这一格，所以这两个值此刻是有的；组合式函数对
  // 空项目名也守得住（`reload` 直接不请求），话题一到那次 watch 会重来。
  projectId: props.projectId ?? '',
  topicId: props.topicId,
  tick: props.refreshTick,
})

const {
  templates,
  add: addTemplate,
  change: changeTemplate,
  remove: removeTemplate,
} = useRoutineTemplates(() => props.projectId ?? '')

/** 模板页上改了一条模板：和存一条规则一样，改上了把这一页收起来（那一条已经改在
 *  列表里了）；没改上就留在这张表上写一句，人再存一次不用重填。 */
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

/** 表单存下去。勾了「存成模板」就把这张表也收成一条 —— 存规则失败时那一条不留下。
 *  两处各自会失败：规则存下了、模板没存上要说一句，人下次找不到那一条。 */
async function onSubmit(payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }) {
  const saved = await submit(payload)
  if (!saved || !payload.keepAsTemplate) return
  if (!(await addTemplate(payload.body))) error.value = t('routines.template.saveFailed')
}

/**
 * 有人点了一句提到这条规则的话里那个名字（`?routine=<id>`）：这一格选中它、展开它最
 * 近三次、滚到它跟前亮一下。
 *
 * 名单可能还在路上——人是从一条旧消息点进来的。所以等它回来再落：`routines` 每读一
 * 次都换一个数组，那个 watch 会重来，落到了一条就不再动（一轮结束加 tick 也会重读，
 * 不能每读一次又滚一下）。那一条不在名单里（已经删了）就什么都不做：从旧消息点过来，
 * 指不到是常态。
 */
const pointed = ref<string | null>(null)

async function pointAt(row: Routine) {
  closeForm()
  closeRecords()
  await toggleRuns(row, true)
  await nextTick()
  void focusRow(`[data-routine="${CSS.escape(row.id)}"]`)
}

watch(
  [() => props.routineId, routines],
  ([id, rows]) => {
    if (!id || pointed.value === id) return
    const row = rows.find((r) => r.id === id)
    if (!row) return
    pointed.value = id
    void pointAt(row)
  },
  { immediate: true }
)
</script>

<template>
  <PanelRoutines
    :routines="routines"
    :runs="runs"
    :open-id="openId"
    :records="records"
    :busy="busy"
    :loading="loading"
    :error="error"
    :form-open="formOpen"
    :editing="editing"
    :form-error="formError"
    :saving="saving"
    :default-room="topicId ?? ''"
    :room-name="roomTitle ?? ''"
    :agent-name="agentName"
    :templates="templates"
    @reload="reload"
    @start-new="startNew"
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
    @open-file="(path) => emit('open-file', path)"
    @form-open-change="(open) => (open ? (formOpen = true) : closeForm())"
    @submit="onSubmit"
    @update-template="(payload) => void onUpdateTemplate(payload)"
    @delete-template="(tpl) => void removeTemplate(tpl.id)"
  />
</template>
