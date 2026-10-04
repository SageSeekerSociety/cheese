<script setup lang="ts">
// 邀请码：看得见、改得动、收得回。只对所有者与管理员开放（接口也这么把关）。
//
// 两处刻意的做法：
// - 调整是「把这一行改成这个样子」：可用人数、有效期、说明一起发回去，不是只发动过的
//   那一项。表单本来就预填着当前值，一起发等于「照这个样子生效」，比猜哪一项被人碰过
//   更好懂。有效期留空 = 永不过期，和新建那张表单同一个说法，所以不需要再多一个开关。
//   说明同理：后端把「不发这一项」读作「别动它」、把 null 读作「清掉」，所以表单必须
//   把自己那份值原样发出去 —— 只发改过的那一项的话，清空说明就等于没清。
// - 撤销要点两下，第二下才是真的。撤销不可逆 —— 码从此对所有人无效、也不再出现在这张
//   表里 —— 一步点到就撤掉的话误触没有回头路。
//
// 「当前使用中的码」不是「列表里第一张」：一张用尽或过期的码就躺在列表第一格上，
// 拿它当当前码，人照着发给下一个人的时候才发现发不出去。判据在 `model.ts`
// （`currentInviteCode`）。
import type { SpaceInviteCode } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { currentInviteCode, inviteCodeStatus } from '../model'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const { t } = useI18n()
const spaceId = Number(route.params.spaceId)

const codes = ref<SpaceInviteCode[]>([])
const loading = ref(false)
// 读失败和「还没有邀请码」是两件事：失败留在页面上，空列表才交给列表自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const busy = ref(false)
const copiedId = ref<number | null>(null)

/** 正在被调整的那一行；null = 没有人在调整。 */
const editingId = ref<number | null>(null)
const editMaxUses = ref('')
const editExpiresOn = ref('')
const editNote = ref('')

/** 已经点过一下撤销、正在等第二下的那一行。 */
const confirmingId = ref<number | null>(null)

const newOpen = ref(false)
const newMaxUses = ref('50')
const newExpiresOn = ref('')
const newNote = ref('')

/** 「当前使用中的码」。没有就是 `null` —— 不拿一张用尽或过期的码顶上。 */
const active = computed(() => currentInviteCode(codes.value))

/** 建码人的显示名。`created_by` 可空（没有 actor 的调用者建的码留着空），
 *  空的那一栏写「未知」，不写空串 —— 空串看起来像「这个人没有名字」。 */
function makerName(item: SpaceInviteCode) {
  const maker = item.createdBy
  if (!maker) return t('spaces.inviteCodes.unknownMaker')
  return maker.nickname || maker.username
}

/** 表单里那份说明，发出去之前的归一：全是空白 = 没有说明（后端也这么读）。 */
function noteFrom(value: string): string | null {
  return value.trim() || null
}

async function refresh() {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const res = await SpacesApi.listInviteCodes(spaceId)
    codes.value = res.data.inviteCodes ?? []
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
    editingId.value = null
    confirmingId.value = null
  }
}

onMounted(refresh)

/** 状态是算出来的，不是存的：库里只有次数与期限，两者都能让一张码失效。
 *  判据本身在 `model.ts`（`inviteCodeStatus`）。 */
function statusOf(item: SpaceInviteCode) {
  return inviteCodeStatus(item)
}

/** 「1 / 50 人已用」；上限 0 是不限。 */
function usage(item: SpaceInviteCode) {
  return item.maxUses > 0
    ? t('spaces.inviteCodes.usage', { used: item.useCount, max: item.maxUses })
    : t('spaces.inviteCodes.usageUnlimited', { used: item.useCount })
}

function formatDate(ms: number) {
  return dayjs(ms).format('YYYY-MM-DD')
}

function asDateInput(ms: number | null) {
  return ms === null ? '' : dayjs(ms).format('YYYY-MM-DD')
}

async function copyCode(item: SpaceInviteCode) {
  try {
    await navigator.clipboard.writeText(item.code)
    copiedId.value = item.id
    setTimeout(() => (copiedId.value = null), 1600)
  } catch {
    // 剪贴板被拒（非安全上下文 / 没授权）——码还在屏幕上，手工选中复制即可。
  }
}

function startEdit(item: SpaceInviteCode) {
  confirmingId.value = null
  editingId.value = item.id
  editMaxUses.value = String(item.maxUses || 1)
  editExpiresOn.value = asDateInput(item.expiresAt)
  // 预填当前说明：保存是「照这个样子生效」，所以这一格必须带着它原来的值 ——
  // 留空保存等于把说明清掉（后端把那读作显式的 null），那是另一件事。
  editNote.value = item.note ?? ''
}

async function saveEdit(item: SpaceInviteCode) {
  if (busy.value) return
  const maxUses = Number(editMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesInvalid'))
    return
  }
  // 后端也挡这一条（会 400），先在本地说明白：把人数调到比已经用掉的还少，码当场就废了。
  if (maxUses < item.useCount) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesBelowUsed', { n: item.useCount }))
    return
  }
  busy.value = true
  try {
    await SpacesApi.updateInviteCode(spaceId, item.id, {
      maxUses,
      // 留空 = 永不过期，所以这里发的是显式的 null（后端把「不发」读作「别动这一项」）。
      expiresAt: editExpiresOn.value ? dayjs(editExpiresOn.value).endOf('day').valueOf() : null,
      // 说明同理：表单带着当前值，所以发出去的就是「这一行现在的说明」；清空 = 清掉。
      note: noteFrom(editNote.value),
    })
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.updated'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.updateFailed'))
  } finally {
    busy.value = false
  }
}

async function revoke(item: SpaceInviteCode) {
  if (busy.value) return
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    editingId.value = null
    return
  }
  busy.value = true
  try {
    await SpacesApi.revokeInviteCode(spaceId, item.id)
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.revoked'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.revokeFailed'))
  } finally {
    busy.value = false
  }
}

async function submitCreate() {
  if (busy.value) return
  const maxUses = Number(newMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesInvalid'))
    return
  }
  busy.value = true
  try {
    await SpacesApi.createInviteCode(spaceId, {
      maxUses,
      // `type="date"` 只给到日，取当天的最后一刻 —— 否则选「今天」当场就是过期的。
      ...(newExpiresOn.value ? { expiresAt: dayjs(newExpiresOn.value).endOf('day').valueOf() } : {}),
      note: noteFrom(newNote.value),
    })
    newOpen.value = false
    newExpiresOn.value = ''
    newNote.value = ''
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.created'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.createFailed'))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <SettingsToolbar>
    <BaseButton
      kind="primary"
      prepend-icon="mdi-plus"
      @click="(newOpen = true), (editingId = null), (confirmingId = null)"
    >
      {{ t('spaces.inviteCodes.create') }}
    </BaseButton>
  </SettingsToolbar>
  <div class="invite-codes">
    <!-- 「当前使用中的码」单列在这里，不和列表第一格混为一谈：用尽或过期的码就躺在
         列表第一格上，照它发出去是发不出去的。一张码都没有时只说一次「暂无邀请码」，
         这一块不出现。 -->
    <div v-if="codes.length > 0" class="current">
      <div class="current__label">{{ t('spaces.inviteCodes.current') }}</div>
      <div v-if="active" class="current__body">
        <code class="current__code">{{ active.code }}</code>
        <BaseButton
          kind="ghost"
          :icon="copiedId === active.id ? 'mdi-check' : 'mdi-content-copy'"
          size="sm"
          :title="t('spaces.inviteCodes.copy')"
          :aria-label="t('spaces.inviteCodes.copy')"
          @click="copyCode(active)"
        />
        <span class="current__meta">{{ usage(active) }}</span>
      </div>
      <div v-else class="current__none">{{ t('spaces.inviteCodes.noneUsable') }}</div>
    </div>

    <div v-if="newOpen" class="settings-card form">
      <div class="form__title">{{ t('spaces.inviteCodes.newTitle') }}</div>
      <div class="form-row">
        <v-text-field
          v-model="newMaxUses"
          autocomplete="off"
          type="number"
          min="1"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('spaces.inviteCodes.maxUses')"
          class="form-row__num"
        />
        <v-text-field
          v-model="newExpiresOn"
          autocomplete="off"
          type="date"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('spaces.inviteCodes.expiresOn')"
          class="form-row__date"
        />
      </div>
      <div class="form-row mt-3">
        <v-text-field
          v-model="newNote"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('spaces.inviteCodes.note')"
        />
      </div>
      <div class="form__actions">
        <BaseButton kind="ghost" :disabled="busy" @click="newOpen = false">{{
          t('spaces.inviteCodes.cancel')
        }}</BaseButton>
        <BaseButton kind="primary" :loading="busy" @click="submitCreate">
          {{ t('spaces.inviteCodes.generate') }}
        </BaseButton>
      </div>
    </div>

    <div v-if="loading" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <BaseLoadError
      v-else-if="failed"
      :title="t('spaces.inviteCodes.toast.loadFailed')"
      :error="errorDetail"
      @retry="refresh"
    />

    <div v-else class="settings-card">
      <v-list v-if="codes.length > 0" class="settings-list" bg-color="transparent">
        <v-list-item v-for="item in codes" :key="item.id">
          <v-list-item-title class="codes__head">
            <span class="codes__text">{{ item.code }}</span>
            <BaseButton
              kind="ghost"
              :icon="copiedId === item.id ? 'mdi-check' : 'mdi-content-copy'"
              size="sm"
              :title="t('spaces.inviteCodes.copy')"
              :aria-label="t('spaces.inviteCodes.copy')"
              @click="copyCode(item)"
            />
            <span class="codes__state" :class="`codes__state--${statusOf(item).key}`">
              <span class="codes__dot" aria-hidden="true" />{{ t(`spaces.inviteCodes.status.${statusOf(item).key}`) }}
            </span>
          </v-list-item-title>

          <div class="codes__meta">
            <span>{{ usage(item) }}</span>
            <span>{{
              item.expiresAt
                ? t('spaces.inviteCodes.expiresAt', { date: formatDate(item.expiresAt) })
                : t('spaces.inviteCodes.neverExpires')
            }}</span>
            <span>
              {{ t('spaces.inviteCodes.maker') }}
              <UserRef v-if="item.createdBy" :handle="item.createdBy.username" :name="makerName(item)" />
              <template v-else>{{ makerName(item) }}</template>
            </span>
          </div>

          <!-- 说明。没写就写「没写说明」而不是留白：留白读起来像这一格坏了，
               而「谁都没写过」是一条真话（每一张在这一格存在之前建的码都是）。 -->
          <div class="codes__note" :class="{ 'codes__note--none': !item.note }">
            {{ item.note || t('spaces.inviteCodes.noNote') }}
          </div>

          <!-- 调整：就地改，不跳页。这一行预填着当前值，保存就是「照这个样子生效」。 -->
          <div v-if="editingId === item.id" class="codes__edit">
            <div class="form-row">
              <v-text-field
                v-model="editMaxUses"
                autocomplete="off"
                type="number"
                min="1"
                density="compact"
                variant="outlined"
                hide-details
                :label="t('spaces.inviteCodes.maxUses')"
                class="form-row__num"
              />
              <v-text-field
                v-model="editExpiresOn"
                autocomplete="off"
                type="date"
                density="compact"
                variant="outlined"
                hide-details
                :label="t('spaces.inviteCodes.expiresOnEdit')"
                class="form-row__date"
              />
            </div>
            <div class="form-row mt-3">
              <v-text-field
                v-model="editNote"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                :label="t('spaces.inviteCodes.noteEdit')"
              />
            </div>
            <div class="form__actions">
              <BaseButton kind="ghost" size="sm" :disabled="busy" @click="editingId = null">
                {{ t('spaces.inviteCodes.cancel') }}
              </BaseButton>
              <BaseButton kind="primary" size="sm" :loading="busy" @click="saveEdit(item)">
                {{ t('spaces.inviteCodes.save') }}
              </BaseButton>
            </div>
          </div>

          <template #append>
            <div class="codes__actions">
              <BaseButton v-if="editingId !== item.id" kind="ghost" size="sm" :disabled="busy" @click="startEdit(item)">
                {{ t('spaces.inviteCodes.edit') }}
              </BaseButton>
              <BaseButton
                size="sm"
                :kind="confirmingId === item.id ? 'danger' : 'ghost'"
                :solid="confirmingId === item.id"
                :disabled="busy"
                @click="revoke(item)"
              >
                {{ confirmingId === item.id ? t('spaces.inviteCodes.confirmRevoke') : t('spaces.inviteCodes.revoke') }}
              </BaseButton>
            </div>
          </template>
        </v-list-item>
      </v-list>

      <p v-else class="settings-empty">{{ t('spaces.inviteCodes.empty') }}</p>
    </div>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.invite-codes {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.current {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--fill);
}

.current__label {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.current__body {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.current__code,
.codes__text {
  color: var(--ink);
  font-family: var(--font-mono);
  font-weight: 600;
  letter-spacing: 0.06em;
}

.current__code {
  font-size: 15px;
  line-height: var(--lh-15);
}

.current__meta,
.current__none {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.form {
  padding: 16px 24px;
}

.form__title {
  margin-bottom: 12px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.form__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 12px;
}

.form-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.form-row__num {
  max-width: 160px;
}

.form-row__date {
  max-width: 240px;
}

.codes__head {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.codes__text {
  font-size: 14px;
  line-height: var(--lh-14);
}

.codes__state {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.codes__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
}

.codes__state--usable {
  color: var(--ok-ink);
}

.codes__state--usable .codes__dot {
  background: var(--ok);
}

.codes__state--exhausted {
  color: var(--danger-ink);
}

.codes__state--exhausted .codes__dot {
  background: var(--danger);
}

.codes__state--expired {
  color: var(--warn-ink);
}

.codes__state--expired .codes__dot {
  background: var(--warn);
}

.codes__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 0;
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.codes__meta > span + span::before {
  margin: 0 6px;
  color: var(--faint);
  content: '·';
}

.codes__note {
  margin-top: 2px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.codes__note--none {
  color: var(--faint);
}

.codes__edit {
  margin-top: 12px;
}

.codes__actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
</style>
