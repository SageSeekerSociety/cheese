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
    <form v-else-if="editing" class="settings-card" novalidate @submit.prevent="save">
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
        <v-btn variant="text" color="on-surface" :disabled="saving" @click="cancel">
          {{ t('account.realName.cancel') }}
        </v-btn>
        <v-btn type="submit" color="primary" variant="flat" :loading="saving">
          {{ t('account.realName.save') }}
        </v-btn>
      </div>
    </form>

    <section v-else-if="record" class="settings-card">
      <div class="settings-card__head">
        <h2 class="settings-card__title">{{ t('account.realName.yours') }}</h2>
        <div class="realname__actions">
          <v-btn
            variant="text"
            color="on-surface"
            :prepend-icon="full ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            :loading="revealing"
            @click="toggleFull"
          >
            {{ full ? t('account.realName.hideFull') : t('account.realName.showFull') }}
          </v-btn>
          <v-btn variant="outlined" color="on-surface" :loading="opening" @click="startEditing">
            {{ t('account.realName.edit') }}
          </v-btn>
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
        <v-btn variant="text" class="realname__delete" :loading="deleting" @click="remove">
          {{ t('account.realName.delete') }}
        </v-btn>
        <span class="realname__foot-note">{{ t('account.realName.deleteNote') }}</span>
      </div>
    </section>

    <section v-else class="settings-card realname__empty">
      <h2 class="t-title">{{ t('account.realName.emptyTitle') }}</h2>
      <p class="realname__empty-body">{{ t('account.realName.emptyBody') }}</p>
      <v-btn color="primary" variant="flat" @click="startEditing">{{ t('account.realName.fill') }}</v-btn>
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
        <p v-if="!logs.length" class="realname__log-empty">
          {{ logsFailed ? t('account.realName.log.loadFailed') : t('account.realName.log.empty') }}
        </p>
        <div
          v-for="(entry, index) in logs"
          :key="`${entry.accessTime}-${index}`"
          class="log-row"
          :class="{ 'log-row--own': isOwnView(entry) }"
        >
          <span v-if="isOwnView(entry)" class="log-row__mark" aria-hidden="true">
            <v-icon icon="mdi-eye-outline" size="16" />
          </span>
          <UserAvatar v-else :avatar="avatarOf(entry)" :name="nameOf(entry)" size="28" class="log-row__avatar" />
          <span class="log-row__body">
            <span class="log-row__what">{{ describe(entry) }}</span>
            <span v-if="entry.accessEntityName" class="log-row__where">
              {{
                t('account.realName.log.where', {
                  name: entry.accessEntityName,
                  kind: entry.accessEntityIsCourse ? t('account.realName.log.course') : t('account.realName.log.space'),
                })
              }}
            </span>
          </span>
          <time class="t-meta" :datetime="new Date(entry.accessTime).toISOString()">{{
            formatTime(entry.accessTime)
          }}</time>
        </div>
        <div v-if="logsHaveMore" class="realname__log-more">
          <v-btn variant="text" color="on-surface" size="small" :loading="loadingLogs" @click="loadLogs(false)">
            {{ t('account.realName.log.more') }}
          </v-btn>
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
import type { RealNameInfo, UserIdentityAccessLog } from '@/network/api/users/types'

import { computed, onMounted, reactive, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'
import { SudoCancelledError, withSudo } from '@/utils/sudo'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'

import UserAvatar from '@/components/common/UserAvatar.vue'
import i18n, { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { UserIdentityAccessType } from '@/network/api/users/types'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useDialog } from '@/plugins/dialog'
import { currentUserId } from '@/services/account'

type Field = keyof RealNameInfo

const FIELDS: { key: Field; label: string; optional: boolean }[] = [
  { key: 'realName', label: 'account.realName.name', optional: false },
  { key: 'studentId', label: 'account.realName.studentId', optional: false },
  { key: 'grade', label: 'account.realName.grade', optional: true },
  { key: 'major', label: 'account.realName.major', optional: true },
  { key: 'className', label: 'account.realName.className', optional: true },
]
const LOG_PAGE = 20

const dialogs = useDialog()
const fail = (error: unknown, fallback: string) => toast.error(requestErrorMessage(error, fallback))

// ---- The record ----

const loaded = ref(false)
const loadFailed = ref(false)
/** What the page shows by default: name and student ID masked. Null when there is no record. */
const record = ref<RealNameInfo | null>(null)
/** The same record in full, once the person has confirmed who they are to see it. */
const full = ref<RealNameInfo | null>(null)
const shown = computed(() => full.value ?? record.value ?? emptyRecord())

function emptyRecord(): RealNameInfo {
  return { realName: '', studentId: '', grade: '', major: '', className: '' }
}

async function load() {
  const userId = currentUserId.value
  if (!userId) return
  try {
    const { data } = await UserApi.getRealNameInfo(userId)
    record.value = data.hasIdentity && data.identity ? data.identity : null
    full.value = null
    loadFailed.value = false
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

const revealing = ref(false)

/** Fetch the record in full. False when the person backed out or it failed. */
async function reveal(): Promise<boolean> {
  const userId = currentUserId.value
  if (!userId) return false
  revealing.value = true
  try {
    const { data } = await withSudo('realname:view', (ticket) => UserApi.getPreciseRealNameInfo(userId, ticket))
    full.value = data.identity ?? null
    // Seeing it in full is itself recorded.
    void loadLogs(true)
    return full.value !== null
  } catch (error) {
    if (!(error instanceof SudoCancelledError)) fail(error, t('account.realName.viewFailed'))
    return false
  } finally {
    revealing.value = false
  }
}

async function toggleFull() {
  if (full.value) full.value = null
  else await reveal()
}

// ---- Editing ----

const editing = ref(false)
const opening = ref(false)
const saving = ref(false)
const attempted = ref(false)
const form = reactive<RealNameInfo>(emptyRecord())

const errors = computed<Partial<Record<Field, string>>>(() => {
  if (!attempted.value) return {}
  return {
    realName: form.realName.trim() ? undefined : t('account.realName.nameRequired'),
    studentId: form.studentId.trim() ? undefined : t('account.realName.studentIdRequired'),
  }
})

/**
 * A masked value cannot go into a field, so changing an existing record starts
 * from it in full; the person confirms who they are once, and the same
 * confirmation covers the save that follows.
 */
async function startEditing() {
  if (record.value && !full.value) {
    opening.value = true
    const ok = await reveal().finally(() => (opening.value = false))
    if (!ok) return
  }
  Object.assign(form, full.value ?? emptyRecord())
  attempted.value = false
  editing.value = true
}

function cancel() {
  editing.value = false
  attempted.value = false
}

async function save() {
  attempted.value = true
  const userId = currentUserId.value
  if (!userId || errors.value.realName || errors.value.studentId || saving.value) return
  const values: RealNameInfo = {
    realName: form.realName.trim(),
    studentId: form.studentId.trim(),
    grade: form.grade.trim(),
    major: form.major.trim(),
    className: form.className.trim(),
  }
  saving.value = true
  try {
    await withSudo('realname:update', (ticket) => UserApi.updateRealNameInfo(userId, values, ticket))
    editing.value = false
    toast.success(t('account.realName.saved'))
    await load()
  } catch (error) {
    if (!(error instanceof SudoCancelledError)) fail(error, t('account.realName.saveFailed'))
  } finally {
    saving.value = false
  }
}

// ---- Deleting ----

const deleting = ref(false)

async function remove() {
  const userId = currentUserId.value
  if (!userId) return
  const confirmed = await dialogs
    .confirm(t('account.realName.deleteBody'), { title: t('account.realName.delete') })
    .wait()
    .catch(() => false)
  if (!confirmed) return
  deleting.value = true
  try {
    await withSudo('realname:delete', (ticket) => UserApi.deleteRealNameInfo(userId, ticket))
    toast.success(t('account.realName.deleted'))
    await load()
  } catch (error) {
    if (!(error instanceof SudoCancelledError)) fail(error, t('account.realName.deleteFailed'))
  } finally {
    deleting.value = false
  }
}

// ---- Who read it ----

const logs = ref<UserIdentityAccessLog[]>([])
const logTotal = ref(0)
const logsNext = ref<number | undefined>(undefined)
const logsHaveMore = ref(false)
const loadingLogs = ref(false)
const logsFailed = ref(false)

async function loadLogs(fromStart: boolean) {
  const userId = currentUserId.value
  if (!userId) return
  loadingLogs.value = true
  try {
    const { data } = await UserApi.getRealNameAccessLogs(userId, fromStart ? undefined : logsNext.value, LOG_PAGE)
    logs.value = fromStart ? data.logs : [...logs.value, ...data.logs]
    logTotal.value = data.page.total ?? logs.value.length
    logsNext.value = data.page.nextStart ?? undefined
    logsHaveMore.value = data.page.hasMore
    logsFailed.value = false
  } catch {
    logsFailed.value = true
  } finally {
    loadingLogs.value = false
  }
}

const isOwnView = (entry: UserIdentityAccessLog) => entry.accessor.id === currentUserId.value

const nameOf = (entry: UserIdentityAccessLog) => entry.accessor.nickname || entry.accessor.username

const avatarOf = (entry: UserIdentityAccessLog) =>
  isChosenAvatar(entry.accessor.avatarId) ? getAvatarUrl(entry.accessor.avatarId) : ''

function describe(entry: UserIdentityAccessLog) {
  if (entry.accessType === UserIdentityAccessType.EXPORT) {
    return t('account.realName.log.exported', { name: nameOf(entry) })
  }
  return isOwnView(entry)
    ? t('account.realName.log.youViewed')
    : t('account.realName.log.viewed', { name: nameOf(entry) })
}

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

onMounted(() => {
  ensureDefaultAvatarId()
  void load()
  void loadLogs(true)
})
</script>

<style scoped src="./settings-card.css"></style>

<style scoped>
.realname {
  max-width: var(--page-w-read);
}

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

.realname__delete {
  color: var(--danger-ink);
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

@media (max-width: 599.98px) {
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
