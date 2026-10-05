<!--
  The real-name page: reads the stored record, saves, deletes, and works out who
  read it. Changing an existing record, confirming who you are, and the delete
  confirmation all happen here; what it shows is RealNameView.vue.
-->
<template>
  <RealNameView
    :loaded="loaded"
    :load-failed="loadFailed"
    :editing="editing"
    :record="record"
    :full="full"
    :revealing="revealing"
    :opening="opening"
    :saving="saving"
    :deleting="deleting"
    :rows="rows"
    :log-total="logTotal"
    :logs-failed="logsFailed"
    :logs-have-more="logsHaveMore"
    :loading-logs="loadingLogs"
    @save="save"
    @cancel="cancel"
    @edit="startEditing"
    @toggle-full="toggleFull"
    @remove="remove"
    @load-more="loadLogs(false)"
  />
</template>

<script setup lang="ts">
import type { RealNameInfo, UserIdentityAccessLog } from '@/network/api/users/types'
import type { RealNameLogRow } from './RealNameView.vue'

import { computed, onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'
import { SudoCancelledError, withSudo } from '@/utils/sudo'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'

import RealNameView from './RealNameView.vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { UserIdentityAccessType } from '@/network/api/users/types'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useDialog } from '@/plugins/dialog'
import { currentUserId } from '@/services/account'

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
  editing.value = true
}

function cancel() {
  editing.value = false
}

async function save(values: RealNameInfo) {
  const userId = currentUserId.value
  if (!userId || saving.value) return
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
    .confirm(t('account.realName.deleteBody'), {
      title: t('account.realName.delete'),
      confirmLabel: t('account.realName.delete'),
      danger: true,
    })
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

/** The log lines, with everything the view needs to draw one worked out here. */
const rows = computed<RealNameLogRow[]>(() =>
  logs.value.map((entry) => ({
    entry,
    name: nameOf(entry),
    avatarUrl: avatarOf(entry),
    isOwn: isOwnView(entry),
    isExport: entry.accessType === UserIdentityAccessType.EXPORT,
  }))
)

onMounted(() => {
  ensureDefaultAvatarId()
  void load()
  void loadLogs(true)
})
</script>
