<!--
  What the real-name page shows (RealName.vue): the record on file, the form for
  filling it in or changing it, and the log of who has read it. Reading and
  saving the record, confirming who the person is, and who read it all happen in
  the page; this half only draws and keeps the form's own fields.
-->
<template>
  <div class="settings-page realname">
    <header>
      <h1 class="t-page-title">{{ t('account.realName.title') }}</h1>
      <p class="settings-page__lede">{{ t('account.realName.lede') }}</p>
    </header>

    <!-- Held at the card's height until the record arrives, so the page does
         not jump when it does. -->
    <section v-if="!loaded" class="settings-card realname__pending" :aria-busy="!loadFailed">
      <p v-if="loadFailed" class="realname__pending-note">{{ t('account.realName.loadFailed') }}</p>
    </section>

    <!-- Editing, or filling in for the first time. Save is the one main action
         while the form is open, so it is the only amber (design-system §1.6). -->
    <form v-else-if="editing" class="settings-card" novalidate @submit.prevent="submit">
      <h2 class="settings-card__title">
        {{ record ? t('account.realName.editTitle') : t('account.realName.fillTitle') }}
      </h2>
      <div v-for="field in FIELDS" :key="field.key" class="srow srow--pair srow--field">
        <label class="srow__k" :for="`realname-${field.key}`">
          {{ t(field.label) }}
          <span v-if="field.optional" class="realname__optional">{{ t('account.realName.optional') }}</span>
        </label>
        <v-text-field
          :id="`realname-${field.key}`"
          v-model="form[field.key]"
          :autocomplete="field.key === 'realName' ? 'name' : 'off'"
          variant="outlined"
          density="compact"
          :error-messages="errors[field.key]"
          hide-details="auto"
        />
      </div>
      <div class="realname__foot realname__foot--form">
        <BaseButton :disabled="saving" @click="emit('cancel')">
          {{ t('account.realName.cancel') }}
        </BaseButton>
        <BaseButton type="submit" kind="primary" :loading="saving">
          {{ t('account.realName.save') }}
        </BaseButton>
      </div>
    </form>

    <section v-else-if="record" class="settings-card">
      <div class="settings-card__head">
        <h2 class="settings-card__title">{{ t('account.realName.yours') }}</h2>
        <div class="realname__actions">
          <BaseButton
            :prepend-icon="full ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            :loading="revealing"
            @click="emit('toggleFull')"
          >
            {{ full ? t('account.realName.hideFull') : t('account.realName.showFull') }}
          </BaseButton>
          <BaseButton kind="secondary" :loading="opening" @click="emit('edit')">
            {{ t('account.realName.edit') }}
          </BaseButton>
        </div>
      </div>
      <div v-for="field in FIELDS" :key="field.key" class="srow srow--pair">
        <span class="srow__k">{{ t(field.label) }}</span>
        <span
          v-if="shown[field.key]"
          class="realname__value"
          :class="{ 'realname__value--mono': field.key === 'studentId' }"
          >{{ shown[field.key] }}</span
        >
        <span v-else class="realname__value realname__value--none">{{ t('account.realName.notGiven') }}</span>
      </div>
      <div class="realname__foot">
        <BaseButton :loading="deleting" @click="emit('remove')">
          {{ t('account.realName.delete') }}
        </BaseButton>
        <span class="realname__foot-note">{{ t('account.realName.deleteNote') }}</span>
      </div>
    </section>

    <section v-else class="settings-card realname__empty">
      <h2 class="t-title">{{ t('account.realName.emptyTitle') }}</h2>
      <p class="realname__empty-body">{{ t('account.realName.emptyBody') }}</p>
      <BaseButton kind="secondary" @click="emit('edit')">{{ t('account.realName.fill') }}</BaseButton>
    </section>

    <!-- Kept after a record is deleted: it says what already happened. -->
    <section
      v-if="loaded && (record || logTotal > 0)"
      class="realname__log"
      :aria-label="t('account.realName.log.title')"
    >
      <div class="realname__log-head">
        <h2 class="t-title">{{ t('account.realName.log.title') }}</h2>
        <span v-if="logTotal > 0" class="t-meta-read t-num">{{ logTotal }}</span>
      </div>
      <div class="settings-card">
        <p v-if="!rows.length" class="realname__log-empty">
          {{ logsFailed ? t('account.realName.log.loadFailed') : t('account.realName.log.empty') }}
        </p>
        <div
          v-for="(row, index) in rows"
          :key="`${row.entry.accessTime}-${index}`"
          class="log-row"
          :class="{ 'log-row--own': row.isOwn }"
        >
          <span v-if="row.isOwn" class="log-row__mark" aria-hidden="true">
            <v-icon icon="mdi-eye-outline" size="16" />
          </span>
          <UserAvatar v-else :avatar="row.avatarUrl" :name="row.name" size="28" class="log-row__avatar" />
          <span class="log-row__body">
            <span v-if="row.isOwn && !row.isExport" class="log-row__what">{{
              t('account.realName.log.youViewed')
            }}</span>
            <i18n-t
              v-else
              :keypath="row.isExport ? 'account.realName.log.exported' : 'account.realName.log.viewed'"
              tag="span"
              class="log-row__what"
            >
              <template #name
                ><UserRef
                  :handle="row.entry.accessor.username"
                  :name="row.name"
                  :to="row.to"
                  @navigate="emit('visitUser', row.to)"
              /></template>
            </i18n-t>
            <span v-if="row.entry.accessEntityName" class="log-row__where">
              {{
                t('account.realName.log.where', {
                  name: row.entry.accessEntityName,
                  kind: t('account.realName.log.space'),
                })
              }}
            </span>
          </span>
          <time class="t-meta" :datetime="new Date(row.entry.accessTime).toISOString()">{{
            formatTime(row.entry.accessTime)
          }}</time>
        </div>
        <div v-if="logsHaveMore" class="realname__log-more">
          <BaseButton size="sm" :loading="loadingLogs" @click="emit('loadMore')">
            {{ t('account.realName.log.more') }}
          </BaseButton>
        </div>
      </div>
    </section>

    <router-link class="realname__policy" :to="{ name: 'LegalPrivacy' }" target="_blank" rel="noopener">
      {{ t('account.realName.privacyPolicy') }}
      <v-icon icon="mdi-open-in-new" size="14" />
    </router-link>
  </div>
</template>

<script setup lang="ts">
import type { UserRefTarget } from '@/lib/userRef'
import type { RealNameInfo, UserIdentityAccessLog } from '@/network/api/users/types'

import { computed, reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRef.vue'
import i18n, { t } from '@/i18n'

type Field = keyof RealNameInfo

const FIELDS: { key: Field; label: string; optional: boolean }[] = [
  { key: 'realName', label: 'account.realName.name', optional: false },
  { key: 'studentId', label: 'account.realName.studentId', optional: false },
  { key: 'grade', label: 'account.realName.grade', optional: true },
  { key: 'major', label: 'account.realName.major', optional: true },
  { key: 'className', label: 'account.realName.className', optional: true },
]

/** One line of the read log, with the reading already done: who it was, what
 *  avatar, where their name goes, and whether it was the person themselves (the
 *  page works that out from the signed-in account and the access type). */
export interface RealNameLogRow {
  entry: UserIdentityAccessLog
  name: string
  avatarUrl: string
  /** Where clicking the person's name goes. Null when there is no handle to follow. */
  to: UserRefTarget | null
  isOwn: boolean
  isExport: boolean
}

const props = defineProps<{
  loaded: boolean
  loadFailed: boolean
  editing: boolean
  /** What the page shows by default: name and student ID masked. Null when there is no record. */
  record: RealNameInfo | null
  /** The same record in full, once the person has confirmed who they are to see it. */
  full: RealNameInfo | null
  revealing: boolean
  opening: boolean
  saving: boolean
  deleting: boolean
  rows: RealNameLogRow[]
  logTotal: number
  logsFailed: boolean
  logsHaveMore: boolean
  loadingLogs: boolean
}>()

const emit = defineEmits<{
  save: [values: RealNameInfo]
  cancel: []
  edit: []
  toggleFull: []
  remove: []
  loadMore: []
  visitUser: [target: UserRefTarget | null]
}>()

function emptyRecord(): RealNameInfo {
  return { realName: '', studentId: '', grade: '', major: '', className: '' }
}

const shown = computed(() => props.full ?? props.record ?? emptyRecord())

// ---- The form ----

const attempted = ref(false)
const form = reactive<RealNameInfo>(emptyRecord())

const errors = computed<Partial<Record<Field, string>>>(() => {
  if (!attempted.value) return {}
  return {
    realName: form.realName.trim() ? undefined : t('account.realName.nameRequired'),
    studentId: form.studentId.trim() ? undefined : t('account.realName.studentIdRequired'),
  }
})

/** Opening the form fills it from the record in full; how the page got that
 *  record (and whether it needed confirming who the person is first) is the
 *  page's business. */
watch(
  () => props.editing,
  (editing) => {
    if (!editing) return
    Object.assign(form, props.full ?? emptyRecord())
    attempted.value = false
  }
)

function submit() {
  attempted.value = true
  if (errors.value.realName || errors.value.studentId || props.saving) return
  emit('save', {
    realName: form.realName.trim(),
    studentId: form.studentId.trim(),
    grade: form.grade.trim(),
    major: form.major.trim(),
    className: form.className.trim(),
  })
}

// ---- Who read it ----

function formatTime(ms: number) {
  const date = new Date(ms)
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return new Intl.DateTimeFormat(i18n.global.locale.value, {
    year: sameYear ? undefined : 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
/* 不再自己设宽度：这一页也在浮层那一条 720 居中的内容列里（SettingsOverlay 的
   `.so__content`），和别的设置页同宽。 */

.realname__pending {
  min-height: 296px;
}

.realname__pending-note {
  padding: 24px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.realname__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 12px;
}

/* A label and what is there, on one line. */
.srow--pair {
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 24px;
  min-height: 0;
  padding: 12px 24px;
}

.srow--pair > .srow__k {
  padding-top: 8px;
}

/* A row holding a field: the label sits on the field's first line. */
.srow--field {
  align-items: start;
}

.srow--field > .srow__k {
  padding-top: 10px;
}

.realname__optional {
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
  color: var(--faint);
}

.realname__value {
  padding-top: 8px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  overflow-wrap: anywhere;
}

.realname__value--mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.realname__value--none {
  color: var(--faint);
}

.realname__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  padding: 12px 16px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}

.realname__foot--form {
  gap: 8px;
  justify-content: flex-end;
  padding: 16px 24px;
  background: var(--canvas);
}

.realname__foot-note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
}

.realname__empty {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
  padding: 32px 24px;
}

.realname__empty .t-title {
  color: var(--ink);
}

.realname__empty-body {
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--muted);
}

.realname__log {
  display: grid;
  gap: 12px;
}

.realname__log-head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.realname__log-head .t-title {
  color: var(--ink);
}

.realname__log-empty {
  padding: 16px 24px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.log-row {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 12px 24px;
  border-top: 1px solid var(--line);
}

.log-row:first-child {
  border-top: 0;
}

.log-row__avatar,
.log-row__mark {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
}

.log-row__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: var(--faint);
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.log-row__body {
  display: flex;
  flex-direction: column;
  flex-grow: 1;
  gap: 2px;
  min-width: 0;
}

.log-row__what {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
}

.log-row--own .log-row__what {
  color: var(--muted);
}

.log-row__where {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}

.log-row time {
  flex-shrink: 0;
}

.realname__log-more {
  display: flex;
  justify-content: center;
  padding: 8px;
  border-top: 1px solid var(--line);
}

.realname__policy {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  justify-self: start;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-decoration: none;
  transition: color var(--dur-quick) var(--ease-standard);
}

.realname__policy:hover {
  color: var(--ink);
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .srow--pair {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    padding: 12px 16px;
  }

  .srow--pair > .srow__k,
  .realname__value {
    padding-top: 0;
  }

  .settings-card__head {
    flex-wrap: wrap;
  }

  .realname__actions {
    justify-content: flex-start;
    margin-top: 0;
    padding: 0 16px 8px;
  }

  .realname__foot--form {
    padding: 12px 16px;
  }

  .realname__empty {
    padding: 24px 16px;
  }

  .log-row {
    padding: 12px 16px;
  }
}
</style>
