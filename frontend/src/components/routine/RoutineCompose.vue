<script setup lang="ts">
// 建一条规则的那两页：左一页填一张表，右边一页从模板里挑一条。项目总览页和房间右侧
// 那一格画的是同一份，差别只有「频道」那一格 —— 项目页要在项目的频道里挑一个，房间里
// 它是定死的（写出来给人看一眼，不让人挑）。
//
// 一页而不是一个弹窗：这里要摆的东西比一张表多（频道、一排模板、底下那两颗），弹窗在
// 窄屏上会挤成一条。它占满所在的容器，容器是页面正文还是房间里的那一格由调用方决定。
//
// 只吃 props：不知道接口在哪儿、也不认识路由。存下去由调用方办（`@save`），出错也由
// 调用方把话递回来（`error`）—— 那句话写在这一页里，人改了字段就地再存一次。
//
// 「时间」和「条件」两颗勾选框看着是两样，其实是同一个 `trigger` 的二选一：一条规则只有
// 一种触发方式。所以勾上这一颗就是取消那一颗，取消勾选是切换，不是「两样都不要」。
import type { Routine, RoutineInput, RoutineTrigger } from '@/lib/routine'
import type { RoutineTemplate } from '@/lib/routineTemplates'

import { computed, reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { routineFreqs, routineWeekdays } from '@/lib/routine'
import { presetTemplates, routineWhenText } from '@/lib/routineTemplates'

const props = withDefaults(
  defineProps<{
    /** 关着的时候组件还在（调用方按这个决定画它还是画那一串规则），所以「开没开」是一个 prop。 */
    modelValue: boolean
    /** 正在改的那一条；`null` 是新建。 */
    routine?: Routine | null
    /** 可选频道。空数组 = 不画选择框（房间里频道是定死的）。 */
    rooms?: { id: string; title: string }[]
    /** 新建时落在哪个频道。`rooms` 空着时就是它。 */
    defaultRoom?: string
    /** 那个定死的频道叫什么（房间里那一格给）：写在「频道」那一行上。 */
    roomName?: string
    /** 人自己存下来的模板（「我存的」那一段）。预置不从这里进。 */
    templates?: RoutineTemplate[]
    saving?: boolean
    error?: string
  }>(),
  {
    routine: null,
    rooms: () => [],
    defaultRoom: '',
    roomName: '',
    templates: () => [],
    saving: false,
    error: '',
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  /** `keepAsTemplate` 是那一颗「存成模板」：存下规则的同时，把这张表也收成一条模板。 */
  (e: 'save', payload: { room: string; body: RoutineInput; keepAsTemplate: boolean }): void
  /** 点了「我存的」那一条上的「删除」。 */
  (e: 'delete-template', template: RoutineTemplate): void
}>()

/** 两页：新建（那张表）和模板（挑一条铺进去）。改一条已有时只有那张表。 */
const tab = ref<'new' | 'template'>('new')

const form = reactive({
  room: '',
  title: '',
  instructions: '',
  note: '',
  trigger: 'schedule' as RoutineTrigger,
  freq: 'weekly',
  time: '09:00',
  weekdays: [0] as number[],
  day: 1,
  minute: 0,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
})

const editing = ref<Routine | null>(null)
/** 「条件」那一颗勾上时用哪一种：勾上「时间」不会忘掉它上一次选的是哪一样。 */
const eventTrigger = ref<RoutineTrigger>('library_file_added')
/** 「存成模板」那一颗。新建时勾得动，改一条已有的时不画（那条规则已经在了）。 */
const asTemplate = ref(false)
/** 点过一次「保存」了没有：缺项的提示只在点过之后才显示，刚打开的一张空表不骂人。 */
const attempted = ref(false)

// 打开的那一下把这一页铺成要编辑的那一条（或新的一条）。关上的这一下不动它：收起动画
// 还在画刚才那一张，半路清空会闪一下。
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const r = props.routine
    editing.value = r
    tab.value = 'new'
    asTemplate.value = false
    attempted.value = false
    const spec = (r?.spec ?? {}) as Record<string, unknown>
    if (r && r.trigger !== 'schedule') eventTrigger.value = r.trigger
    Object.assign(form, {
      room: r?.topic_id || props.defaultRoom || props.rooms[0]?.id || '',
      title: r?.title ?? '',
      instructions: r?.instructions ?? '',
      note: r?.note ?? '',
      trigger: r?.trigger ?? 'schedule',
      freq: (spec.freq as string) ?? 'weekly',
      time: (spec.time as string) ?? '09:00',
      weekdays: (spec.weekdays as number[]) ?? [0],
      day: (spec.day as number) ?? 1,
      minute: (spec.minute as number) ?? 0,
      timezone: r?.timezone ?? form.timezone,
    })
  },
  { immediate: true }
)

const presets = computed(() => presetTemplates())
/** 时间那一栏画不画：勾着「时间」的时候才画。 */
const onTime = computed(() => form.trigger === 'schedule')
const conditionOptions = computed(() =>
  (['library_file_added', 'task_closed', 'card_accepted'] as RoutineTrigger[]).map((value) => ({
    value,
    title: t(`routines.trigger.${value}`),
  }))
)

/** 勾「时间」或勾「条件」：都是把这一条换到那一头去，另一颗跟着灭。 */
function pickTrigger(next: RoutineTrigger) {
  form.trigger = next
  if (next !== 'schedule') eventTrigger.value = next
}
/** 取消勾选不做任何事：一条规则必须有触发方式，勾选框在这里是一颗二选一。 */
function toggleTime(on: unknown) {
  if (on) pickTrigger('schedule')
}
function toggleCondition(on: unknown) {
  if (on) pickTrigger(eventTrigger.value)
}

/** 表单里的几样人话 → 后端要的 `spec`。 */
function specFromForm(): Record<string, unknown> {
  if (form.trigger !== 'schedule') return { scope: scopeOfRoutine() }
  return { ...whenFromForm(), ...keptFromRoutine() }
}

function whenFromForm(): Record<string, unknown> {
  if (form.freq === 'hourly') return { freq: 'hourly', minute: Number(form.minute) }
  if (form.freq === 'weekly') return { freq: 'weekly', time: form.time, weekdays: [...form.weekdays] }
  if (form.freq === 'monthly') return { freq: 'monthly', time: form.time, day: Number(form.day) }
  return { freq: 'daily', time: form.time }
}

/**
 * 条件那几条的 `scope`：这一页不画这一格（规则长在哪个频道就在哪个频道里看事情），
 * 所以新的按「只看这个频道」；改一条本来写着「整个项目」的，把原来那一份原样留着，
 * 不因为这里没有这一格就悄悄改成别的意思。
 */
function scopeOfRoutine(): string {
  const spec = (editing.value?.spec ?? {}) as Record<string, unknown>
  return typeof spec.scope === 'string' ? spec.scope : 'room'
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

/** 一条模板「什么时候跑」那一行：按读者此刻的语言现算（模板本身只存 `spec`）。 */
function whenText(tpl: RoutineTemplate): string {
  return routineWhenText(tpl.trigger, tpl.spec)
}

/** 挑一条模板：把它那几格铺进表里，然后翻到那张表上让人看一眼、再改。 */
function applyTemplate(tpl: RoutineTemplate) {
  form.title = tpl.title
  form.instructions = tpl.goal
  form.note = tpl.note ?? ''
  form.trigger = tpl.trigger
  if (tpl.trigger === 'schedule') {
    const spec = tpl.spec
    form.freq = (spec.freq as string) ?? 'daily'
    form.time = (spec.time as string) ?? '09:00'
    form.weekdays = (spec.weekdays as number[]) ?? [0]
    form.day = (spec.day as number) ?? 1
    form.minute = (spec.minute as number) ?? 0
  } else {
    eventTrigger.value = tpl.trigger
  }
  tab.value = 'new'
}

/**
 * 缺了什么就说一句，摆在这一页底下那一行里（和存失败的那句同一个位置）。
 *
 * 不做成「把那颗按钮灰掉」：灰掉不说是哪里不对，人只能自己猜。目标那一格是唯一必填的
 * （后端也是这么判的），每周一次得挑至少一天（不然它一年也不会跑）。
 */
const localError = computed(() => {
  if (!attempted.value) return ''
  if (!form.instructions.trim()) return t('routines.form.goalRequired')
  if (form.trigger === 'schedule' && form.freq === 'weekly' && !form.weekdays.length) {
    return t('routines.form.weekdaysRequired')
  }
  return ''
})

function submit() {
  attempted.value = true
  if (localError.value) return
  emit('save', {
    room: form.room,
    body: {
      title: form.title,
      instructions: form.instructions,
      note: form.note,
      trigger: form.trigger,
      spec: specFromForm(),
      timezone: form.timezone,
    },
    keepAsTemplate: asTemplate.value,
  })
}
</script>

<template>
  <div v-if="modelValue" class="compose">
    <p v-if="editing" class="compose__edit">{{ t('routines.editTitle', { title: editing.title }) }}</p>
    <div v-else class="compose__tabs" role="tablist" :aria-label="t('routines.compose.tab.label')">
      <button
        type="button"
        role="tab"
        class="compose__tab"
        :class="{ 'compose__tab--on': tab === 'new' }"
        :aria-selected="tab === 'new'"
        @click="tab = 'new'"
      >
        {{ t('routines.compose.tab.new') }}
      </button>
      <button
        type="button"
        role="tab"
        class="compose__tab"
        :class="{ 'compose__tab--on': tab === 'template' }"
        :aria-selected="tab === 'template'"
        @click="tab = 'template'"
      >
        {{ t('routines.compose.tab.template') }}
      </button>
    </div>

    <div v-if="tab === 'template'" class="compose__body">
      <p class="compose__section">{{ t('routines.template.preset') }}</p>
      <ul class="tpl-list">
        <li v-for="tpl in presets" :key="tpl.id" class="tpl">
          <button type="button" class="tpl__pick" @click="applyTemplate(tpl)">
            <span class="tpl__title">{{ tpl.title }}</span>
            <span class="tpl__meta">{{ whenText(tpl) }} · {{ tpl.goal }}</span>
          </button>
        </li>
      </ul>

      <template v-if="templates.length">
        <p class="compose__section">
          {{ t('routines.template.mine') }}
          <span class="compose__section-note">{{ t('routines.template.mineNote') }}</span>
        </p>
        <ul class="tpl-list">
          <li v-for="tpl in templates" :key="tpl.id" class="tpl">
            <button type="button" class="tpl__pick" @click="applyTemplate(tpl)">
              <span class="tpl__title">{{ tpl.title }}</span>
              <span class="tpl__meta">{{ whenText(tpl) }} · {{ tpl.goal }}</span>
            </button>
          </li>
        </ul>
      </template>
    </div>

    <div v-else class="compose__body">
      <v-select
        v-if="rooms.length"
        v-model="form.room"
        autocomplete="off"
        :items="rooms"
        item-title="title"
        item-value="id"
        :label="t('routines.form.room')"
        class="mb-3"
      />
      <!-- 房间里那一格：频道是定死的，写出来给人看一眼就够，不让挑。 -->
      <div v-else-if="roomName" class="compose__room">
        <span class="compose__room-label">{{ t('routines.form.room') }}</span>
        <span class="compose__room-name">{{ roomName }}</span>
      </div>

      <p class="t-meta c-faint compose__hint">{{ t('routines.form.autoHint') }}</p>

      <v-text-field v-model="form.title" autocomplete="off" :label="t('routines.form.name')" class="mb-3" />
      <v-textarea
        v-model="form.instructions"
        autocomplete="off"
        :label="t('routines.form.goal')"
        :placeholder="t('routines.form.goalPlaceholder')"
        rows="3"
        auto-grow
        required
      />
      <v-textarea
        v-model="form.note"
        autocomplete="off"
        :label="t('routines.form.note')"
        :placeholder="t('routines.form.notePlaceholder')"
        rows="2"
        auto-grow
      />

      <v-checkbox
        :model-value="onTime"
        autocomplete="off"
        :label="t('routines.form.timeOn')"
        hide-details
        density="compact"
        class="compose__switch"
        @update:model-value="toggleTime"
      />
      <template v-if="onTime">
        <div class="compose__row">
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

      <v-checkbox
        :model-value="!onTime"
        autocomplete="off"
        :label="t('routines.form.conditionOn')"
        hide-details
        density="compact"
        class="compose__switch"
        @update:model-value="toggleCondition"
      />
      <div v-if="!onTime" class="when-list">
        <button
          v-for="opt in conditionOptions"
          :key="opt.value"
          type="button"
          class="when"
          :class="{ 'when--on': form.trigger === opt.value }"
          :aria-pressed="form.trigger === opt.value"
          @click="pickTrigger(opt.value)"
        >
          {{ opt.title }}
        </button>
      </div>
    </div>

    <div class="compose__foot">
      <v-checkbox
        v-if="!editing"
        v-model="asTemplate"
        autocomplete="off"
        :label="t('routines.form.keepTemplate')"
        hide-details
        density="compact"
        class="compose__keep"
      />
      <p v-if="error || localError" role="alert" class="t-body c-danger compose__error">
        {{ error || localError }}
      </p>
      <BaseButton :disabled="saving" @click="emit('update:modelValue', false)">{{ t('routines.cancel') }}</BaseButton>
      <BaseButton kind="primary" :loading="saving" @click="submit">
        {{ t('routines.save') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.compose {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.compose__edit {
  margin: 8px 0 12px;
  color: var(--text);
  font-weight: 600;
}
/* 两页的页签：选中的那一格是 --ink 的字加一条 --ink 的下划线，和页头下面那一行一样
   （它说的是「你在哪」，不是这一页的主操作）。 */
.compose__tabs {
  display: flex;
  gap: 20px;
  margin: 0 0 12px;
  border-bottom: 1px solid var(--line);
}
.compose__tab {
  margin-bottom: -1px;
  padding: 8px 0;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}
.compose__tab:hover {
  color: var(--ink);
}
.compose__tab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.compose__tab--on {
  border-bottom-color: var(--ink);
  color: var(--ink);
  font-weight: 600;
}
.compose__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
/* 频道那一行：房间里那一格用。一条浅浅的底，一行读完「这条规则长在哪儿」。 */
.compose__room {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.compose__room-label {
  color: var(--muted);
  font-size: 13px;
}
.compose__room-name {
  color: var(--text);
  font-weight: 600;
}
.compose__hint {
  margin-bottom: 12px;
}
.compose__section {
  margin: 8px 0;
  color: var(--muted);
  font-size: 13px;
}
.compose__section-note {
  color: var(--muted);
}
.compose__switch {
  margin-top: 8px;
}
.compose__row {
  display: flex;
  gap: 8px;
}
/* 手机上三格并排每格只剩一百来像素，「频率」的下拉和时间都挤不下：竖着排。字段之间的
   空隙由每一格底下的 details 行给。 */
@media (max-width: 959.98px) {
  .compose__row {
    flex-direction: column;
    gap: 0;
  }
}
.compose__foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  margin-top: 12px;
  border-top: 1px solid var(--line);
}
.compose__error {
  flex: 1;
  margin: 0;
}
/* 一条模板一行：名字一行，什么时候跑加它要干什么一行。整行点得动。 */
.tpl-list {
  list-style: none;
  padding: 0;
  margin: 0 0 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tpl {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.tpl__pick {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 10px 12px;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.tpl__pick:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.tpl__title {
  color: var(--text);
  font-weight: 600;
}
.tpl__meta {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 条件那三条：一条一行，选中的那一条用琥珀描边（这一页里那个「就是它」的样子）。 */
.when-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.when {
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.when--on {
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
</style>
