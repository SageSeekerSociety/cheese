<script setup lang="ts">
// 新建 / 修改一条规则的那张表单。项目总览页和房间右侧那一格用同一张。
//
// 表单里填的是「人话」的几样（多久一次、几点、星期几、范围），发出去的是后端要的
// `spec` —— 从人话到 `spec` 的翻译只在这里做一次（`specFromForm`），两个入口不会
// 各翻译一套。
//
// 只吃 props：不知道接口在哪儿、也不认识路由。存下去由调用方办（`@save`），出错也由
// 调用方把话递回来（`error`）—— 那句话要写在这张表里，人改了字段就地再存一次。
import type { Routine, RoutineInput, RoutineTrigger } from '@/lib/routine'

import { reactive, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { routineFreqs, routineTriggers, routineWeekdays } from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    /** 关着的时候组件还在（对话框有自己的退场动画），所以「开没开」是一个 prop。 */
    modelValue: boolean
    /** 正在改的那一条；`null` 是新建。 */
    routine?: Routine | null
    /** 可选房间。空数组 = 不画这一格（房间面板里房间是定死的：规则就长在这个房间里）。 */
    rooms?: { id: string; title: string }[]
    /** 新建时落在哪个房间。`rooms` 空着时就是它。 */
    defaultRoom?: string
    saving?: boolean
    error?: string
  }>(),
  { routine: null, rooms: () => [], defaultRoom: '', saving: false, error: '' }
)

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'save', payload: { room: string; body: RoutineInput }): void
}>()

const form = reactive({
  room: '',
  title: '',
  instructions: '',
  context_scope: '',
  output_dir: '',
  trigger: 'schedule' as RoutineTrigger,
  freq: 'weekly',
  time: '09:00',
  weekdays: [0] as number[],
  day: 1,
  minute: 0,
  scope: 'room',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
})

const editing = ref<Routine | null>(null)
const title = ref('')

// 打开的那一下把表单铺成这一条的样子。关上的这一下不动它：收起动画还在画刚才那张，
// 半路清空会闪一下。
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const r = props.routine
    editing.value = r
    title.value = r ? t('routines.editTitle', { title: r.title }) : t('routines.newTitle')
    const spec = (r?.spec ?? {}) as Record<string, unknown>
    Object.assign(form, {
      room: r?.topic_id || props.defaultRoom || props.rooms[0]?.id || '',
      title: r?.title ?? '',
      instructions: r?.instructions ?? '',
      context_scope: r?.context_scope ?? '',
      output_dir: r?.output_dir ?? '',
      trigger: r?.trigger ?? 'schedule',
      freq: (spec.freq as string) ?? 'weekly',
      time: (spec.time as string) ?? '09:00',
      weekdays: (spec.weekdays as number[]) ?? [0],
      day: (spec.day as number) ?? 1,
      minute: (spec.minute as number) ?? 0,
      scope: (spec.scope as string) ?? 'room',
      timezone: r?.timezone ?? form.timezone,
    })
  },
  { immediate: true }
)

/** 表单里的几样人话 → 后端要的 `spec`。 */
function specFromForm(): Record<string, unknown> {
  if (form.trigger !== 'schedule') return { scope: form.scope }
  return { ...whenFromForm(), ...keptFromRoutine() }
}

function whenFromForm(): Record<string, unknown> {
  if (form.freq === 'hourly') return { freq: 'hourly', minute: Number(form.minute) }
  if (form.freq === 'weekly') return { freq: 'weekly', time: form.time, weekdays: [...form.weekdays] }
  if (form.freq === 'monthly') return { freq: 'monthly', time: form.time, day: Number(form.day) }
  return { freq: 'daily', time: form.time }
}

/**
 * 定时规则里表单没有画出来的那一项：`feedback_batch`（每次附上几条待分诊反馈，
 * 见后端 `routine/schedule.py`）。改钟点时把整份 `spec` 换成表单这几格的话，
 * 它就被悄悄删掉，分诊从此不再附反馈，而界面上什么也看不出来。
 */
function keptFromRoutine(): Record<string, unknown> {
  const spec = (editing.value?.spec ?? {}) as Record<string, unknown>
  return 'feedback_batch' in spec ? { feedback_batch: spec.feedback_batch } : {}
}

function submit() {
  emit('save', {
    room: form.room,
    body: {
      title: form.title,
      instructions: form.instructions,
      context_scope: form.context_scope,
      output_dir: form.output_dir,
      trigger: form.trigger,
      spec: specFromForm(),
      timezone: form.timezone,
    },
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="title"
    :primary-label="t('routines.save')"
    :primary-loading="saving"
    :max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="submit"
  >
    <template v-if="modelValue">
      <v-select
        v-if="rooms.length"
        v-model="form.room"
        autocomplete="off"
        :items="rooms"
        item-title="title"
        item-value="id"
        :label="t('routines.form.room')"
        :hint="t('routines.form.roomHint')"
        persistent-hint
        class="mb-3"
      />
      <v-text-field
        v-model="form.title"
        autocomplete="off"
        :label="t('routines.form.name')"
        :placeholder="t('routines.form.namePlaceholder')"
      />
      <v-textarea
        v-model="form.instructions"
        autocomplete="off"
        :label="t('routines.form.instructions')"
        rows="3"
        auto-grow
        :placeholder="t('routines.form.instructionsPlaceholder')"
      />
      <v-text-field
        v-model="form.context_scope"
        autocomplete="off"
        :label="t('routines.form.context')"
        :placeholder="t('routines.form.contextPlaceholder')"
      />
      <v-text-field
        v-model="form.output_dir"
        autocomplete="off"
        :label="t('routines.form.outputDir')"
        :placeholder="t('routines.form.outputDirPlaceholder')"
      />
      <v-select
        v-model="form.trigger"
        autocomplete="off"
        :items="routineTriggers()"
        :label="t('routines.form.trigger')"
      />
      <template v-if="form.trigger === 'schedule'">
        <div class="routine-form__row">
          <v-select v-model="form.freq" autocomplete="off" :items="routineFreqs()" :label="t('routines.form.freq')" />
          <v-text-field
            v-if="form.freq !== 'hourly'"
            v-model="form.time"
            autocomplete="off"
            type="time"
            :label="t('routines.form.time')"
          />
          <v-text-field
            v-else
            v-model.number="form.minute"
            autocomplete="off"
            type="number"
            min="0"
            max="59"
            :label="t('routines.form.minute')"
          />
          <v-text-field
            v-if="form.freq === 'monthly'"
            v-model.number="form.day"
            autocomplete="off"
            type="number"
            min="1"
            max="31"
            :label="t('routines.form.day')"
          />
        </div>
        <v-chip-group v-if="form.freq === 'weekly'" v-model="form.weekdays" multiple column class="mb-2">
          <v-chip v-for="(d, i) in routineWeekdays()" :key="d" :value="i" filter size="small">{{ d }}</v-chip>
        </v-chip-group>
        <v-text-field v-model="form.timezone" autocomplete="off" :label="t('routines.form.timezone')" />
      </template>
      <v-select
        v-else-if="form.trigger !== 'library_file_added'"
        v-model="form.scope"
        autocomplete="off"
        :items="[
          { value: 'room', title: t('routines.form.scopeRoom') },
          { value: 'project', title: t('routines.form.scopeProject') },
        ]"
        :label="t('routines.form.scope')"
      />
      <p class="t-meta c-faint">{{ t('routines.form.machineNote') }}</p>
      <p v-if="error" role="alert" class="t-body c-danger mt-2">{{ error }}</p>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.routine-form__row {
  display: flex;
  gap: 8px;
}
/* 手机上三格并排每格只剩一百来像素，「频率」的下拉和时间都挤不下：竖着排。字段之间的
   空隙由每一格底下的 details 行给。 */
@media (max-width: 959.98px) {
  .routine-form__row {
    flex-direction: column;
    gap: 0;
  }
}
</style>
