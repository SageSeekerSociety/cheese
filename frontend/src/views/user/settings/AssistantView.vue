<script setup lang="ts">
/**
 * 「芝士」设置页的渲染面：只吃 props、只发事件。取数与保存在同目录的 `Assistant.vue`。
 *
 * 五块，自上而下：需要确认（几类勾选，和自己添加的条目），无需确认（自己添加的条目），
 * 记忆（芝士记得的关于你的事：开关、逐条改和删、全部清除），
 * 自动化（芝士到时间或在某件事发生时自己做的事，一个列表：什么时候、做什么、现在怎样；
 * 只做一次的做完就不列了，记录留在对话里），
 * 最近的操作（做了的、请人确认过的，请人确认的写原因）。
 */
import type {
  AssistantActivityItem,
  AssistantMemory,
  AssistantMemoryItem,
  AssistantRoutine,
  AssistantRoutineForm,
  AssistantRules,
} from '@/api'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import { fmtPoints } from '@/lib/creditUsage'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  rules: AssistantRules | null
  memory: AssistantMemory | null
  routines: AssistantRoutine[]
  recent: AssistantActivityItem[]
  loading: boolean
  failed: boolean
  saving: boolean
  saved: boolean
  error: string
}>()

const emit = defineEmits<{
  retry: []
  toggle: [code: string]
  addItem: [list: 'ask' | 'allow', text: string]
  removeItem: [list: 'ask' | 'allow', index: number]
  routine: [id: string, action: 'pause' | 'resume' | 'delete']
  memoryEnabled: [on: boolean]
  memoryEdit: [name: string, description: string, text: string]
  memoryDelete: [name: string]
  memoryClear: []
  openConversation: [id: string]
  /** 存下一条：`null` 是新建；回来的那句是失败原因，空串是成功。 */
  saveRoutine: [id: string | null, body: AssistantRoutineForm]
}>()

const { t, locale } = useI18n()

const drafts = ref({ ask: '', allow: '' })

function add(list: 'ask' | 'allow') {
  const text = drafts.value[list].trim()
  if (!text) return
  emit('addItem', list, text)
  drafts.value[list] = ''
}

/** 正在改的那条记忆，和改到一半的两格。 */
const editing = ref<{ name: string; description: string; text: string } | null>(null)
const clearing = ref(false)

function edit(item: AssistantMemoryItem) {
  editing.value = { name: item.name, description: item.description, text: item.text }
}

function saveEdit() {
  const now = editing.value
  if (!now || !now.description.trim() || !now.text.trim()) return
  emit('memoryEdit', now.name, now.description.trim(), now.text.trim())
  editing.value = null
}

function confirmClear() {
  emit('memoryClear')
  clearing.value = false
}

/** 要删除的那条例行事务：先确认。 */
const deleting = ref<AssistantRoutine | null>(null)

/** 正在填的那张表：`null` 是没开，`{routine: null}` 是新建。 */
const form = ref<{ routine: AssistantRoutine | null } | null>(null)

/** 那七条条件，一颗一颗列出来，和改动清单一一对上。界面上「任务被采纳」和
    「任务被退回」是两颗；存的时候是后端说的「审阅有结果」（``decided``）加上
    结果，见 ``pickCondition``。 */
const CONDITION_TRIGGERS = ['mention', 'review', 'filed', 'accepted', 'rejected', 'due', 'invite'] as const

/** 0=周一 … 6=周日，和频道那张表、后端同一套编号。 */
const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

/** The form's blanks, as the person left them. Every one but the target may
 *  be left empty: the backend fills blanks in by 芝士 before anything runs.
 *  A schedule carries the moment its frequency needs and no other. */
const draft = ref({
  title: '',
  note: '',
  explain: '',
  onTime: false,
  freq: 'daily',
  time: '08:00',
  minute: 0,
  weekdays: [0] as number[],
  day: 1,
  condition: '',
  task: '',
  outcome: '',
  once: false,
})
/** Said after a save the backend refused (the target is the one required
 *  field): shown in the form, not as a toast somewhere else. */
const formError = ref('')

function openForm(routine: AssistantRoutine | null) {
  formError.value = ''
  const spec = (routine?.spec ?? {}) as Record<string, unknown>
  draft.value = {
    title: routine?.title ?? '',
    note: routine?.note ?? '',
    explain: '',
    onTime: routine ? routine.trigger === 'schedule' : false,
    freq: (spec.freq as string) ?? 'daily',
    time: (spec.time as string) ?? '08:00',
    minute: (spec.minute as number) ?? 0,
    weekdays: (spec.weekdays as number[]) ?? [0],
    day: (spec.day as number) ?? 1,
    condition: routine && routine.trigger !== 'schedule' ? routine.trigger : '',
    task: '',
    outcome: (spec.outcome as string) ?? '',
    once: routine?.once ?? false,
  }
  form.value = { routine }
}

/** What the frequency needs to be one unambiguous moment: hourly names its
 *  minute, weekly its weekdays, monthly its day, once its moment. Empty of
 *  the rest — a weekly with a day of the month would be read as the wrong
 *  question. */
function momentOf(): Record<string, unknown> {
  if (!draft.value.onTime) return {}
  switch (draft.value.freq) {
    case 'hourly':
      return { minute: draft.value.minute }
    case 'weekly':
      return { time: draft.value.time, weekdays: [...draft.value.weekdays].sort() }
    case 'monthly':
      return { time: draft.value.time, day: draft.value.day }
    case 'once':
      return { at: draft.value.time }
    default:
      return { time: draft.value.time }
  }
}

function submitForm() {
  const now = form.value
  if (!now) return
  if (!draft.value.note.trim()) {
    formError.value = t('account.assistant.routines.form.goalRequired')
    return
  }
  if (draft.value.onTime && draft.value.freq === 'weekly' && !draft.value.weekdays.length) {
    formError.value = t('account.assistant.routines.form.weekdaysRequired')
    return
  }
  // 「哪个任务」留空就是「我提的任何任务」：那是这一族规则的常见写法，不该拦。
  const event = !draft.value.onTime && draft.value.condition
  formError.value = ''
  const moment = momentOf()
  emit('saveRoutine', now.routine?.id ?? null, {
    title: draft.value.title.trim(),
    note: draft.value.note.trim(),
    explain: draft.value.explain.trim(),
    trigger: draft.value.onTime ? 'schedule' : draft.value.condition,
    freq: draft.value.onTime ? draft.value.freq : '',
    time: (moment.time as string) ?? '',
    minute: (moment.minute as number) ?? null,
    weekdays: (moment.weekdays as number[]) ?? null,
    day: (moment.day as number) ?? null,
    at: (moment.at as string) ?? '',
    timezone: '',
    mine: Boolean(event) && !draft.value.task.trim(),
    task: event ? draft.value.task.trim() : '',
    outcome: event && draft.value.condition === 'decided' ? draft.value.outcome : '',
    once: event && draft.value.once,
  })
  form.value = null
}

/** 「任务被采纳」和「任务被退回」是同一件事的两种结果（后端叫 ``decided``，
 *  ``outcome`` 说哪一种）。界面上仍是两颗，和清单的七条一一对上。 */
function pickCondition(trigger: string) {
  draft.value.onTime = false
  if (draft.value.condition === trigger && draft.value.outcome === '') {
    draft.value.condition = ''
    draft.value.outcome = ''
    return
  }
  draft.value.condition = trigger === 'accepted' || trigger === 'rejected' ? 'decided' : trigger
  draft.value.outcome = trigger === 'accepted' || trigger === 'rejected' ? trigger : ''
}

/** 一颗按钮是不是正选着：``accepted``/``rejected`` 看 ``outcome``，其余看
 *  触发方式本身。 */
function picked(trigger: string): boolean {
  return trigger === 'accepted' || trigger === 'rejected'
    ? draft.value.condition === 'decided' && draft.value.outcome === trigger
    : draft.value.condition === trigger
}

function pickTime() {
  draft.value.onTime = true
  draft.value.condition = ''
}

/** 星期几：按 0=周一 存，和频道那张表、后端同一套编号。 */
function toggleWeekday(day: number) {
  const days = draft.value.weekdays
  draft.value.weekdays = days.includes(day) ? days.filter((d) => d !== day) : [...days, day]
}

/** 「只有一次」 and「生成中」 and「没补上」 each read their own way. */
function stateText(r: AssistantRoutine): string {
  if (r.state === 'filling') {
    return t('account.assistant.routines.filling', { missing: r.missing.join('、') })
  }
  return state(r)
}
// One that fires once leaves the list once it has run: what it did is in its conversation.
// Those still waiting come first.
const listed = computed(() => [
  ...props.routines.filter((r) => r.once && r.state !== 'done'),
  ...props.routines.filter((r) => !r.once),
])

function confirmDelete() {
  if (deleting.value) emit('routine', deleting.value.id, 'delete')
  deleting.value = null
}

function when(at: string): string {
  return new Intl.DateTimeFormat(locale.value, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(at))
}

function state(r: AssistantRoutine): string {
  if (r.once && r.state === 'active') return t('account.assistant.routines.waiting')
  if (r.state === 'paused') return t(`account.assistant.routines.paused.${r.paused || 'person'}`)
  return r.nextRunAt ? t('account.assistant.routines.next', { time: when(r.nextRunAt) }) : ''
}

function last(r: AssistantRoutine): string {
  const run = r.lastRun
  if (!run) return ''
  if (run.status === 'done' && run.asked) return t('account.assistant.routines.last.asked', { n: run.asked })
  return t(`account.assistant.routines.last.${run.status}`)
}
</script>

<template>
  <div class="settings-page">
    <header class="ast__head">
      <div>
        <h1 class="t-page-title">{{ t('account.assistant.title') }}</h1>
        <p class="settings-page__lede">{{ t('account.assistant.lede') }}</p>
      </div>
      <SaveStatus
        class="ast__status"
        :saving="saving"
        :saved="saved"
        :error="error"
        :failed-text="t('account.assistant.saveFailed')"
      />
    </header>

    <div v-if="loading" class="settings-card ast__loading">
      <v-progress-circular indeterminate size="22" :aria-label="t('account.assistant.loading')" />
    </div>
    <BaseLoadError v-else-if="failed" :title="t('account.assistant.loadFailed')" @retry="emit('retry')" />
    <template v-else-if="rules">
      <section v-for="list in ['ask', 'allow'] as const" :key="list" class="settings-card">
        <h2 class="settings-card__title">{{ t(`account.assistant.${list}.title`) }}</h2>
        <p class="settings-card__desc">{{ t(`account.assistant.${list}.desc`) }}</p>
        <ul v-if="list === 'ask'" class="ast__checks">
          <li v-for="c in rules.categories" :key="c.code">
            <label class="ast__check">
              <input
                type="checkbox"
                :checked="rules.ask.includes(c.code)"
                :disabled="saving"
                @change="emit('toggle', c.code)"
              />
              <span>{{ c.label }}</span>
            </label>
          </li>
        </ul>
        <ul class="ast__items">
          <li
            v-for="(item, i) in list === 'ask' ? rules.askItems : rules.allowItems"
            :key="`${i}-${item}`"
            class="ast__entry"
          >
            <span class="ast__entry-text">{{ item }}</span>
            <BaseButton kind="ghost" size="sm" :disabled="saving" @click="emit('removeItem', list, i)">
              {{ t('account.assistant.items.remove') }}
            </BaseButton>
          </li>
          <li v-if="list === 'allow' && !rules.allowItems.length" class="ast__entry ast__entry--empty">
            {{ t('account.assistant.items.empty') }}
          </li>
        </ul>
        <form class="ast__add" @submit.prevent="add(list)">
          <input
            :id="`ast-${list}`"
            v-model="drafts[list]"
            class="ast__text"
            maxlength="200"
            autocomplete="off"
            :aria-label="t(`account.assistant.${list}.title`)"
            :placeholder="t(`account.assistant.${list}.hint`)"
          />
          <BaseButton kind="secondary" size="sm" type="submit" :disabled="!drafts[list].trim() || saving">
            {{ t('account.assistant.items.add') }}
          </BaseButton>
        </form>
      </section>

      <section v-if="memory" class="settings-card">
        <div class="ast__memory-head">
          <h2 class="settings-card__title ast__title-row">
            <span>{{ t('account.assistant.memory.title') }}</span>
            <span v-if="memory.items.length" class="ast__count t-num">{{
              t('account.assistant.memory.count', { n: memory.items.length })
            }}</span>
          </h2>
          <v-switch
            id="ast-memory"
            :model-value="memory.enabled"
            :disabled="saving"
            :aria-label="t('account.assistant.memory.use')"
            color="primary"
            density="compact"
            inset
            hide-details
            @update:model-value="(v) => emit('memoryEnabled', v === true)"
          />
        </div>
        <p class="settings-card__desc">
          {{ memory.enabled ? t('account.assistant.memory.desc') : t('account.assistant.memory.off') }}
        </p>
        <p v-if="!memory.items.length" class="ast__none">{{ t('account.assistant.memory.empty') }}</p>
        <ul v-else class="ast__recent">
          <li v-for="item in memory.items" :key="item.name" class="ast__routine">
            <form v-if="editing?.name === item.name" class="ast__memory-edit" @submit.prevent="saveEdit">
              <input
                :id="`ast-memory-${item.name}-description`"
                v-model="editing.description"
                class="ast__text"
                maxlength="150"
                autocomplete="off"
                :aria-label="t('account.assistant.memory.description')"
              />
              <textarea
                :id="`ast-memory-${item.name}-text`"
                v-model="editing.text"
                class="ast__text ast__textarea"
                rows="3"
                maxlength="1000"
                autocomplete="off"
                :aria-label="t('account.assistant.memory.text')"
              />
              <div class="ast__actions">
                <BaseButton kind="ghost" size="sm" @click="editing = null">
                  {{ t('account.assistant.routines.cancel') }}
                </BaseButton>
                <BaseButton
                  kind="secondary"
                  size="sm"
                  type="submit"
                  :disabled="saving || !editing.description.trim() || !editing.text.trim()"
                >
                  {{ t('account.assistant.memory.save') }}
                </BaseButton>
              </div>
            </form>
            <template v-else>
              <div class="ast__body">
                <p class="ast__summary">{{ item.description }}</p>
                <p class="ast__reason ast__memory-text">{{ item.text }}</p>
              </div>
              <div class="ast__actions">
                <BaseButton kind="ghost" size="sm" :disabled="saving" @click="edit(item)">
                  {{ t('account.assistant.memory.edit') }}
                </BaseButton>
                <BaseButton kind="ghost" size="sm" :disabled="saving" @click="emit('memoryDelete', item.name)">
                  {{ t('account.assistant.items.remove') }}
                </BaseButton>
              </div>
            </template>
          </li>
        </ul>
        <div v-if="memory.items.length" class="ast__memory-foot">
          <BaseButton kind="ghost" size="sm" :disabled="saving" @click="clearing = true">
            {{ t('account.assistant.memory.clear') }}
          </BaseButton>
        </div>
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title ast__title-row">
          <span>{{ t('account.assistant.routines.title') }}</span>
          <BaseButton v-if="!form" kind="primary" size="sm" @click="openForm(null)">
            {{ t('account.assistant.routines.actionNew') }}
          </BaseButton>
        </h2>
        <p class="settings-card__desc">{{ t('account.assistant.routines.desc') }}</p>

        <!-- 那一张表：新建、修改共用。只有「目标」必填，其余留空由芝士补。 -->
        <form v-if="form" class="ast__form" @submit.prevent="submitForm">
          <label class="ast__field">
            <span>{{ t('account.assistant.routines.form.title') }}</span>
            <input v-model="draft.title" maxlength="120" autocomplete="off" />
          </label>
          <label class="ast__field">
            <span>{{ t('account.assistant.routines.form.note') }} *</span>
            <textarea v-model="draft.note" rows="3" maxlength="4000" autocomplete="off" />
          </label>
          <label class="ast__field">
            <span>{{ t('account.assistant.routines.form.explain') }}</span>
            <textarea v-model="draft.explain" rows="2" maxlength="4000" autocomplete="off" />
          </label>
          <div class="ast__switch" role="radiogroup" :aria-label="t('account.assistant.routines.form.whenLabel')">
            <label>
              <input type="radio" name="ast-when" :checked="draft.onTime" @change="pickTime" />
              {{ t('account.assistant.routines.form.onTime') }}
            </label>
            <label>
              <input type="radio" name="ast-when" :checked="!draft.onTime" @change="draft.onTime = false" />
              {{ t('account.assistant.routines.form.onCondition') }}
            </label>
          </div>
          <div v-if="draft.onTime" class="ast__row3">
            <label class="ast__field">
              <span>{{ t('account.assistant.routines.form.freq') }}</span>
              <select v-model="draft.freq">
                <option value="hourly">{{ t('account.assistant.routines.form.hourly') }}</option>
                <option value="daily">{{ t('account.assistant.routines.form.daily') }}</option>
                <option value="weekly">{{ t('account.assistant.routines.form.weekly') }}</option>
                <option value="monthly">{{ t('account.assistant.routines.form.monthly') }}</option>
                <option value="once">{{ t('account.assistant.routines.form.once') }}</option>
              </select>
            </label>
            <!-- 每种频率只问它自己那一刻：每时问第几分钟，每周问星期几，
                 每月问几号，其余问几点。留空的一项由芝士补。 -->
            <label v-if="draft.freq === 'hourly'" class="ast__field">
              <span>{{ t('account.assistant.routines.form.minute') }}</span>
              <input v-model.number="draft.minute" type="number" min="0" max="59" autocomplete="off" />
            </label>
            <label v-else-if="draft.freq === 'monthly'" class="ast__field">
              <span>{{ t('account.assistant.routines.form.day') }}</span>
              <input v-model.number="draft.day" type="number" min="1" max="31" autocomplete="off" />
            </label>
            <label v-if="draft.freq !== 'hourly'" class="ast__field">
              <span>{{ t('account.assistant.routines.form.time') }}</span>
              <input
                v-model="draft.time"
                :type="draft.freq === 'once' ? 'datetime-local' : 'time'"
                autocomplete="off"
              />
            </label>
          </div>
          <div v-else class="ast__when-list">
            <button
              v-for="trigger in CONDITION_TRIGGERS"
              :key="trigger"
              type="button"
              class="ast__when"
              :class="{ 'ast__when--on': picked(trigger) }"
              :aria-pressed="picked(trigger)"
              @click="pickCondition(trigger)"
            >
              {{ t(`account.assistant.routines.form.when.${trigger}`) }}
            </button>
          </div>
          <div v-if="draft.onTime && draft.freq === 'weekly'" class="ast__when-list">
            <button
              v-for="(day, i) in WEEKDAYS"
              :key="day"
              type="button"
              class="ast__when"
              :class="{ 'ast__when--on': draft.weekdays.includes(i) }"
              :aria-pressed="draft.weekdays.includes(i)"
              @click="toggleWeekday(i)"
            >
              {{ t(`routines.weekday.${day}`) }}
            </button>
          </div>
          <!-- 「filed」「decided」等的是某个任务的审阅：填任务地址栏里的那个引用
               （如 ``ml-course/tasks/12``）；留空就是「我提的任务」。 -->
          <label
            v-if="!draft.onTime && (draft.condition === 'filed' || draft.condition === 'decided')"
            class="ast__field"
          >
            <span>{{ t('account.assistant.routines.form.task') }}</span>
            <input
              v-model="draft.task"
              :placeholder="t('account.assistant.routines.form.taskPlaceholder')"
              autocomplete="off"
            />
          </label>
          <label v-if="!draft.onTime" class="ast__check">
            <input v-model="draft.once" type="checkbox" />
            {{ t('account.assistant.routines.form.onceOnly') }}
          </label>
          <p class="ast__hint">{{ t('account.assistant.routines.form.blankHint') }}</p>
          <p v-if="formError" role="alert" class="ast__reason c-danger">{{ formError }}</p>
          <div class="ast__actions">
            <BaseButton kind="ghost" size="sm" type="button" @click="form = null">
              {{ t('account.assistant.routines.cancel') }}
            </BaseButton>
            <BaseButton kind="primary" size="sm" type="submit" :disabled="saving">
              {{ t('account.assistant.routines.save') }}
            </BaseButton>
          </div>
        </form>

        <p v-if="!form && !listed.length" class="ast__none">{{ t('account.assistant.routines.empty') }}</p>
        <ul v-else-if="!form" class="ast__recent">
          <li v-for="r in listed" :key="r.id" class="ast__routine">
            <div class="ast__body">
              <p class="ast__summary ast__when">{{ r.when }}</p>
              <p class="ast__note">{{ r.note || r.title }}</p>
              <p class="ast__meta">
                <span
                  v-if="stateText(r)"
                  :class="{ ast__paused: r.state === 'paused', ast__filling: r.state === 'filling' }"
                  >{{ stateText(r) }}</span
                >
                <span v-if="last(r)">{{ last(r) }}</span>
                <span v-if="r.weekCredits > 0" class="t-num">{{
                  t('account.assistant.routines.cost', { n: fmtPoints(r.weekCredits, locale) })
                }}</span>
              </p>
            </div>
            <div class="ast__actions">
              <BaseButton
                v-if="(r.once ? r.conversationId : r.lastRun?.conversationId) ?? null"
                kind="ghost"
                size="sm"
                @click="emit('openConversation', (r.once ? r.conversationId : r.lastRun?.conversationId) ?? '')"
              >
                {{ t('account.assistant.routines.view') }}
              </BaseButton>
              <BaseButton kind="ghost" size="sm" :disabled="saving" @click="openForm(r)">
                {{ t('account.assistant.routines.edit') }}
              </BaseButton>
              <!-- A rule that fires once is either waiting or done: nothing to
                   pause. A filling one is stopped only by 芝士 giving up. -->
              <BaseButton
                v-if="r.state === 'paused' || (r.state === 'active' && !r.once)"
                kind="ghost"
                size="sm"
                :disabled="saving"
                @click="emit('routine', r.id, r.state === 'paused' ? 'resume' : 'pause')"
              >
                {{
                  r.state === 'paused' ? t('account.assistant.routines.resume') : t('account.assistant.routines.pause')
                }}
              </BaseButton>
              <BaseButton
                v-if="r.once"
                kind="ghost"
                size="sm"
                :disabled="saving"
                @click="emit('routine', r.id, 'delete')"
              >
                {{ t('account.assistant.routines.cancel') }}
              </BaseButton>
              <BaseButton v-else kind="ghost" size="sm" :disabled="saving" @click="deleting = r">
                {{ t('account.assistant.routines.delete') }}
              </BaseButton>
            </div>
          </li>
        </ul>
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.assistant.recent.title') }}</h2>
        <p v-if="!recent.length" class="settings-card__desc">{{ t('account.assistant.recent.empty') }}</p>
        <ul v-else class="ast__recent">
          <li v-for="item in recent" :key="item.id" class="ast__item">
            <span class="ast__chip" :class="`ast__chip--${item.verdict}`">{{
              item.verdict === 'allowed' ? t('account.assistant.recent.done') : t('account.assistant.recent.asked')
            }}</span>
            <div class="ast__body">
              <p class="ast__summary">{{ item.summary }}</p>
              <p v-if="item.verdict === 'asked' && item.reason" class="ast__reason">
                {{ t('account.assistant.recent.why', { reason: item.reason }) }}
              </p>
            </div>
            <time class="ast__time" :datetime="item.at">{{ relTime(item.at) }}</time>
          </li>
        </ul>
      </section>
    </template>

    <ConfirmDialog
      :model-value="deleting !== null"
      :title="t('account.assistant.routines.deleteTitle', { title: deleting?.title ?? '' })"
      :confirm-label="t('account.assistant.routines.delete')"
      :cancel-label="t('account.assistant.routines.cancel')"
      danger
      @update:model-value="(open: boolean) => !open && (deleting = null)"
      @confirm="confirmDelete"
      @cancel="deleting = null"
    >
      {{ t('account.assistant.routines.deleteBody') }}
    </ConfirmDialog>
    <ConfirmDialog
      :model-value="clearing"
      :title="t('account.assistant.memory.clearTitle')"
      :confirm-label="t('account.assistant.memory.clear')"
      :cancel-label="t('account.assistant.routines.cancel')"
      danger
      @update:model-value="(open: boolean) => !open && (clearing = false)"
      @confirm="confirmClear"
      @cancel="clearing = false"
    >
      {{ t('account.assistant.memory.clearBody', { n: memory?.items.length ?? 0 }) }}
    </ConfirmDialog>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.ast__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}

.ast__loading {
  display: flex;
  padding: 32px;
  justify-content: center;
}

.ast__checks {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0 16px;
  padding: 0 24px 8px;
  margin: 0;
  list-style: none;
}

.ast__check {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  cursor: pointer;
}

.ast__check input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--accent);
}

.ast__items {
  padding: 0;
  margin: 0;
  list-style: none;
}

.ast__entry {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 6px 24px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  border-top: 1px solid var(--line);
}

.ast__entry-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.ast__entry--empty {
  padding-block: 10px;
  color: var(--muted);
}

.ast__add {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 12px 24px 20px;
  border-top: 1px solid var(--line);
}

.ast__text {
  box-sizing: border-box;
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.ast__text:focus {
  outline: none;
  border-color: var(--accent);
}

.ast__title-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  /* 标题右侧那颗「＋ 新建」推到边上 */
  justify-content: space-between;
  padding-right: 24px;
}

.ast__title-row > span:first-child {
  margin-right: auto;
}

.ast__count {
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ast__none {
  padding: 0 24px 20px;
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.ast__recent {
  padding: 0;
  margin: 0;
  list-style: none;
}

.ast__item {
  display: grid;
  /* The chip column is as wide as its longest label, so every summary starts at the same edge. */
  grid-template-columns: 6.5em minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  padding: 12px 24px;
  border-top: 1px solid var(--line);
}

.ast__routine {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 12px 24px;
  border-top: 1px solid var(--line);
}

.ast__memory-head {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding-right: 24px;
}

.ast__memory-head .settings-card__title {
  margin-bottom: 0;
}

.ast__memory-text {
  white-space: pre-line;
  overflow-wrap: anywhere;
}

.ast__memory-edit {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
}

.ast__textarea {
  resize: vertical;
}

.ast__memory-foot {
  display: flex;
  justify-content: flex-end;
  padding: 8px 24px 16px;
  border-top: 1px solid var(--line);
}

.ast__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}

.ast__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ast__paused {
  color: var(--text);
}

/* 芝士还在补它：这一行说的是「差什么」，不是「停了」。 */
.ast__filling {
  color: var(--accent-ink);
}

/* 新建 / 修改那一张表：只有「目标」必填，其余留空由芝士补。 */
.ast__form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 24px 16px;
}

.ast__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ast__field input,
.ast__field textarea,
.ast__field select {
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
}

.ast__field textarea {
  resize: vertical;
}

.ast__switch {
  display: flex;
  gap: 16px;
  align-items: center;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
}

.ast__switch label {
  display: flex;
  gap: 6px;
  align-items: center;
}

.ast__row3 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.ast__when-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ast__when {
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
}

.ast__when--on {
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.ast__hint {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.ast__chip {
  justify-self: start;
  padding: 1px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
  border-radius: 999px;
}

.ast__chip--allowed {
  color: var(--ink);
  background: var(--fill);
}

.ast__chip--asked {
  color: var(--accent-ink);
  background: var(--accent-wash, var(--fill));
}

.ast__body {
  min-width: 0;
}

.ast__summary {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: pre-line;
  overflow-wrap: anywhere;
}

.ast__when {
  font-weight: 600;
}

.ast__note {
  margin: 2px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ast__reason {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ast__time {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: nowrap;
}

/* The card's own padding narrows on a phone (`settings-card.css`): these blocks follow it. */
@media (max-width: 767.98px) {
  .ast__checks,
  .ast__entry,
  .ast__add,
  .ast__none,
  .ast__item,
  .ast__routine,
  .ast__memory-foot {
    padding-inline: 16px;
  }

  .ast__memory-head {
    padding-right: 16px;
  }

  .ast__routine {
    flex-direction: column;
  }

  .ast__form {
    padding-inline: 16px;
  }

  .ast__actions {
    justify-content: flex-start;
  }
}
</style>
