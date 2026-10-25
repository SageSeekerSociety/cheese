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
        </h2>
        <p class="settings-card__desc">{{ t('account.assistant.routines.desc') }}</p>
        <p v-if="!listed.length" class="ast__none">{{ t('account.assistant.routines.empty') }}</p>
        <ul v-else class="ast__recent">
          <li v-for="r in listed" :key="r.id" class="ast__routine">
            <div class="ast__body">
              <p class="ast__summary ast__when">{{ r.when }}</p>
              <p class="ast__note">{{ r.note || r.title }}</p>
              <p class="ast__meta">
                <span v-if="state(r)" :class="{ ast__paused: r.state === 'paused' }">{{ state(r) }}</span>
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
              <BaseButton
                v-if="r.state === 'paused' || !r.once"
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

  .ast__actions {
    justify-content: flex-start;
  }
}
</style>
