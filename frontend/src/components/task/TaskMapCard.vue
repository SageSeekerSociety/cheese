<script setup lang="ts">
// 任务的卡片，任务概览最上面那一块：是什么、到哪了、等谁、名词，每栏一两句，没内容
// 的栏不画。AI 队友每轮干完按需改它，做这件任务的人也可以在这里直接改。「来源」那一栏
// 是概览里紧挨着它的那一行（`TaskSourceLine`），平台读出来的，不在卡片里。
import type { MapCard, MapCardChange, MapCardView } from '@/types/taskMapCard'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  view: MapCardView | null
  memberNames: Record<string, string>
  /** 等谁一栏能选的人。 */
  people: { handle: string; name: string }[]
  saving: boolean
  error: string | null
}>()

const emit = defineEmits<{
  (e: 'save', change: MapCardChange): void
}>()

const card = computed<MapCard | null>(() => props.view?.card ?? null)
const filled = computed(
  () =>
    !!card.value &&
    !!(card.value.what || card.value.progress || card.value.waiting_on.length || card.value.terms.length)
)

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
// ---- 编辑 ----
const editing = ref(false)
const draft = ref<Required<MapCardChange>>({ what: '', progress: '', waiting_on: [], terms: [] })
function startEditing() {
  const c = card.value
  draft.value = {
    what: c?.what ?? '',
    progress: c?.progress ?? '',
    waiting_on: (c?.waiting_on ?? []).map((w) => ({ ...w })),
    terms: (c?.terms ?? []).map((w) => ({ ...w })),
  }
  editing.value = true
}
function save() {
  emit('save', {
    what: draft.value.what.trim(),
    progress: draft.value.progress.trim(),
    waiting_on: draft.value.waiting_on.filter((w) => w.handle && w.ask.trim()),
    terms: draft.value.terms.filter((w) => w.term.trim() && w.meaning.trim()),
  })
}
// 存好了就收起编辑；存失败留在编辑里，写的东西还在。别人这时改了卡片（推送来新的
// 一份）不收起：正在写的不能被冲掉。
watch(
  () => props.saving,
  (now, was) => {
    if (was && !now && !props.error) editing.value = false
  }
)
const choices = computed(() => props.people.map((p) => ({ title: p.name, value: p.handle })))
</script>

<template>
  <section class="map-card" data-testid="task-map-card">
    <template v-if="!editing">
      <dl v-if="filled" class="map-card__rows">
        <template v-if="card?.what">
          <dt class="t-eyebrow">{{ t('work.task.mapCard.what') }}</dt>
          <dd class="t-body map-card__what" data-testid="map-card-what">{{ card.what }}</dd>
        </template>
        <template v-if="card?.progress">
          <dt class="t-eyebrow">{{ t('work.task.mapCard.progress') }}</dt>
          <dd class="t-body map-card__line" data-testid="map-card-progress">
            <span class="status-dot" :class="card.waiting_on.length ? 'status-dot--warn' : 'status-dot--ok'" />
            <span>{{ card.progress }}</span>
          </dd>
        </template>
        <template v-if="card?.waiting_on.length">
          <dt class="t-eyebrow">{{ t('work.task.mapCard.waitingOn') }}</dt>
          <dd class="map-card__list" data-testid="map-card-waiting">
            <p v-for="(w, i) in card.waiting_on" :key="i" class="t-body map-card__item">
              <span class="map-card__who">@{{ nameOf(w.handle) }}</span> {{ w.ask }}
            </p>
          </dd>
        </template>
        <template v-if="card?.terms.length">
          <dt class="t-eyebrow">{{ t('work.task.mapCard.terms') }}</dt>
          <dd class="map-card__list" data-testid="map-card-terms">
            <p v-for="(term, i) in card.terms" :key="i" class="t-body map-card__item">
              <span class="map-card__who">{{ term.term }}</span
              >{{ t('work.task.mapCard.termSep') }}{{ term.meaning }}
            </p>
          </dd>
        </template>
      </dl>
      <p v-if="!filled" class="t-body c-muted map-card__empty">{{ t('work.task.mapCard.empty') }}</p>
      <div v-if="card || view?.can_edit" class="map-card__foot">
        <span v-if="card" class="t-meta c-faint">{{
          t('work.task.mapCard.updated', { name: nameOf(card.updated_by), when: relTime(card.updated_at) })
        }}</span>
        <BaseButton v-if="view?.can_edit" size="sm" data-testid="map-card-edit" @click="startEditing">
          {{ t('work.task.mapCard.edit') }}
        </BaseButton>
      </div>
    </template>

    <form v-else class="map-card__form" data-testid="map-card-form" @submit.prevent="save">
      <v-text-field
        v-model="draft.what"
        :label="t('work.task.mapCard.what')"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        maxlength="200"
      />
      <v-text-field
        v-model="draft.progress"
        :label="t('work.task.mapCard.progress')"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        maxlength="200"
      />
      <fieldset class="map-card__group">
        <legend class="t-meta c-faint">{{ t('work.task.mapCard.waitingOn') }}</legend>
        <div v-for="(w, i) in draft.waiting_on" :key="i" class="map-card__pair">
          <v-select
            v-model="w.handle"
            :items="choices"
            :label="t('work.task.mapCard.person')"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            class="map-card__person"
          />
          <v-text-field
            v-model="w.ask"
            :label="t('work.task.mapCard.ask')"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            maxlength="120"
          />
          <BaseButton
            icon="mdi-close"
            size="sm"
            :title="t('work.task.mapCard.remove')"
            :aria-label="t('work.task.mapCard.remove')"
            @click="draft.waiting_on.splice(i, 1)"
          />
        </div>
        <BaseButton
          v-if="draft.waiting_on.length < 5"
          size="sm"
          @click="draft.waiting_on.push({ handle: '', ask: '' })"
        >
          {{ t('work.task.mapCard.add') }}
        </BaseButton>
      </fieldset>
      <fieldset class="map-card__group">
        <legend class="t-meta c-faint">{{ t('work.task.mapCard.terms') }}</legend>
        <div v-for="(term, i) in draft.terms" :key="i" class="map-card__pair">
          <v-text-field
            v-model="term.term"
            :label="t('work.task.mapCard.term')"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            maxlength="40"
            class="map-card__person"
          />
          <v-text-field
            v-model="term.meaning"
            :label="t('work.task.mapCard.meaning')"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            maxlength="160"
          />
          <BaseButton
            icon="mdi-close"
            size="sm"
            :title="t('work.task.mapCard.remove')"
            :aria-label="t('work.task.mapCard.remove')"
            @click="draft.terms.splice(i, 1)"
          />
        </div>
        <BaseButton v-if="draft.terms.length < 2" size="sm" @click="draft.terms.push({ term: '', meaning: '' })">
          {{ t('work.task.mapCard.add') }}
        </BaseButton>
      </fieldset>
      <p v-if="error" class="t-meta map-card__error" role="alert">{{ error }}</p>
      <div class="map-card__actions">
        <BaseButton size="sm" @click="editing = false">{{ t('work.task.mapCard.cancel') }}</BaseButton>
        <BaseButton kind="primary" size="sm" :loading="saving" data-testid="map-card-save" @click="save">
          {{ t('work.task.mapCard.save') }}
        </BaseButton>
      </div>
    </form>
  </section>
</template>

<style scoped>
.map-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.map-card__rows {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 12px;
  margin: 0;
}
.map-card__rows dt {
  padding-top: 2px;
}
.map-card__rows dd {
  min-width: 0;
  margin: 0;
}
.map-card__what {
  color: var(--ink);
}
.map-card__line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.map-card__line .status-dot {
  transform: translateY(-2px);
}
.map-card__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.map-card__item {
  margin: 0;
}
.map-card__who {
  font-weight: 600;
  color: var(--ink);
}
.map-card__empty {
  margin: 0;
}
.map-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.map-card__form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}
.map-card__group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
  margin: 0;
  padding: 0;
  border: 0;
}
.map-card__group legend {
  margin-bottom: 8px;
}
.map-card__pair {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.map-card__person {
  flex: 0 0 160px;
}
.map-card__error {
  margin: 0;
  color: var(--danger-ink);
}
.map-card__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
