<script setup lang="ts">
// 邀请码页「画 + 交互」的那一半。所有状态由容器持有（下面这些 defineModel），
// 它自己只做纯展示与本地交互：调整表单的开合、撤销的两下确认、复制反馈、日期与状态的
// 念法。真正落网的事儿（读列表、改、撤、建）都 emit 给容器。
//
// 「谁建的码」那一颗走 components/common/UserRef.vue（只认 props），去处由容器用
// userRefTo 算好传进来 —— 这里不读路由、不连 API，因此在没装路由/没连 API 的树里也能渲染。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceInviteCode } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { currentInviteCode, inviteCodeStatus } from '../model'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRef.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'

const props = defineProps<{
  codes: SpaceInviteCode[]
  loading: boolean
  /** 读失败：替换掉列表那一格，不落进「暂无邀请码」。 */
  failed: boolean
  errorDetail: string | null
  busy: boolean
  /** 「谁建的码」那一颗的去处；容器按 handle 算好传进来。 */
  userRefTo: (item: SpaceInviteCode) => UserRefTarget | null
}>()

const emit = defineEmits<{
  refresh: []
  create: []
  saveEdit: [item: SpaceInviteCode]
  revoke: [item: SpaceInviteCode]
  navigateRef: [target: UserRefTarget | null]
}>()

const newOpen = defineModel<boolean>('newOpen', { required: true })
const confirmingId = defineModel<number | null>('confirmingId', { required: true })
const editingId = defineModel<number | null>('editingId', { required: true })
const copiedId = defineModel<number | null>('copiedId', { required: true })
const newMaxUses = defineModel<string>('newMaxUses', { required: true })
const newExpiresOn = defineModel<string>('newExpiresOn', { required: true })
const newNote = defineModel<string>('newNote', { required: true })
const editMaxUses = defineModel<string>('editMaxUses', { required: true })
const editExpiresOn = defineModel<string>('editExpiresOn', { required: true })
const editNote = defineModel<string>('editNote', { required: true })

const { t } = useI18n()

/** 「当前使用中的码」。没有就是 `null` —— 不拿一张用尽或过期的码顶上。 */
const active = computed(() => currentInviteCode(props.codes))

/** 建码人的显示名。空的那一栏写「未知」，不写空串。 */
function makerName(item: SpaceInviteCode) {
  const maker = item.createdBy
  if (!maker) return t('spaces.inviteCodes.unknownMaker')
  return maker.nickname || maker.username
}

/** 状态是算出来的，不是存的。 */
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
  // 预填当前说明：保存是「照这个样子生效」，所以这一格必须带着它原来的值。
  editNote.value = item.note ?? ''
}

/** 撤销点两下：第一下只是把这一行切到「确认撤销」，第二下才请容器发请求。 */
function revoke(item: SpaceInviteCode) {
  if (props.busy) return
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    editingId.value = null
    return
  }
  emit('revoke', item)
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
        <BaseButton kind="primary" :loading="busy" @click="emit('create')">
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
      @retry="emit('refresh')"
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
              <UserRef
                v-if="item.createdBy"
                :handle="item.createdBy.username"
                :name="makerName(item)"
                :to="userRefTo(item)"
                @navigate="emit('navigateRef', userRefTo(item))"
              />
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
              <BaseButton kind="primary" size="sm" :loading="busy" @click="emit('saveEdit', item)">
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

      <BaseEmptyState v-else size="inline" class="settings-empty" :title="t('spaces.inviteCodes.empty')" />
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
