<script setup lang="ts">
/**
 * 「芝士」设置页的渲染面：只吃 props、只发事件。取数与保存在同目录的 `Assistant.vue`。
 *
 * 三块，自上而下：一定先问我（几类事，勾上就每次都先问），用自己的话补充（可以直接做、
 * 要先问我两段，芝士的把关会读），最近替你做的事（做了的、先问了的，问的写原因）。
 */
import type { AssistantActivityItem, AssistantRules } from '@/api'

import { ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  rules: AssistantRules | null
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
  saveWords: [words: { allowText: string; askText: string }]
}>()

const allowText = ref('')
const askText = ref('')
watch(
  () => props.rules,
  (now) => {
    allowText.value = now?.allowText ?? ''
    askText.value = now?.askText ?? ''
  },
  { immediate: true }
)

const changed = () =>
  props.rules !== null && (allowText.value !== props.rules.allowText || askText.value !== props.rules.askText)
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
      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.assistant.always.title') }}</h2>
        <p class="settings-card__desc">{{ t('account.assistant.always.desc') }}</p>
        <ul class="ast__checks">
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
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.assistant.words.title') }}</h2>
        <p class="settings-card__desc">{{ t('account.assistant.words.desc') }}</p>
        <div class="ast__words">
          <label for="ast-allow" class="ast__label">{{ t('account.assistant.words.allow') }}</label>
          <textarea
            id="ast-allow"
            v-model="allowText"
            class="ast__text"
            rows="3"
            maxlength="2000"
            autocomplete="off"
            :placeholder="t('account.assistant.words.allowHint')"
          />
          <label for="ast-ask" class="ast__label">{{ t('account.assistant.words.ask') }}</label>
          <textarea
            id="ast-ask"
            v-model="askText"
            class="ast__text"
            rows="3"
            maxlength="2000"
            autocomplete="off"
            :placeholder="t('account.assistant.words.askHint')"
          />
          <div>
            <BaseButton
              kind="primary"
              size="sm"
              :disabled="!changed()"
              :loading="saving"
              @click="emit('saveWords', { allowText: allowText.trim(), askText: askText.trim() })"
            >
              {{ t('account.assistant.words.save') }}
            </BaseButton>
          </div>
        </div>
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
  padding: 0 24px 12px;
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

.ast__words {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 24px 20px;
}

.ast__label {
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
  color: var(--ink);
}

.ast__text {
  box-sizing: border-box;
  width: 100%;
  padding: 8px 10px;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  resize: vertical;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.ast__text:focus {
  outline: none;
  border-color: var(--accent);
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

.ast__summary {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: pre-line;
  overflow-wrap: anywhere;
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
  .ast__words {
    padding-inline: 16px;
  }

  .ast__item {
    padding-inline: 16px;
  }
}
</style>
