<!--
  「模型连接」的画法（#2991）：你的 Claude Code 在各台设备上登录的账号或模型服务，每一份
  保存在哪台设备、上次检查和上次使用的时间，以及重新检查、改名、解除和使用自定义模型服务；
  还有保存在账号里的 API key 和模型服务，每一份的密钥后四位、服务地址、模型和上次检查，
  以及添加、测试、改名和解除。取数和动作在同目录的 ModelConnections.vue。
-->
<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('account.settings.modelConnections') }}</h1>
      <p class="settings-page__lede">{{ t('account.modelConnections.lede') }}</p>
    </header>

    <p v-if="error" role="alert" class="mc__notice c-danger">{{ error }}</p>

    <section class="settings-card" :aria-label="t('account.settings.modelConnections')">
      <div class="settings-card__head mc-head">
        <div class="settings-card__title">Claude Code</div>
        <div class="mc-actions">
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" @click="openHeld('api_key')">
            {{ t('account.modelConnections.addApiKey') }}
          </BaseButton>
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" @click="openHeld('model_service')">
            {{ t('account.modelConnections.addAccountService') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="openService">
            {{ t('account.modelConnections.addService') }}
          </BaseButton>
        </div>
      </div>

      <div v-if="loading && !connections.length && !held.length" class="d-flex justify-center py-6">
        <v-progress-circular indeterminate size="24" />
      </div>
      <BaseEmptyState
        v-else-if="!connections.length && !held.length"
        size="inline"
        class="settings-empty"
        :title="t('account.modelConnections.empty')"
      />

      <div v-for="h in held" :key="h.id" class="mc-row" :data-connection="h.id">
        <div class="mc-row__head">
          <div class="mc-row__id">
            <div class="mc-row__name">{{ h.name || heldLabel(h) }}</div>
            <div v-if="h.name" class="mc-row__sub">{{ heldLabel(h) }}</div>
            <div v-if="h.service_host" class="mc-row__sub">
              {{ t('account.modelConnections.serviceHost', { host: h.service_host }) }}
            </div>
          </div>
          <span class="mc-state" :class="h.check_error ? 'mc-state--failed' : 'mc-state--ok'">
            <span class="mc-state__dot" aria-hidden="true" />{{
              h.check_error ? t('account.modelConnections.checkFailedState') : t('account.modelConnections.ready')
            }}
          </span>
        </div>
        <dl class="mc-spec">
          <dt>{{ t('account.modelConnections.storedOn') }}</dt>
          <dd>{{ t('account.modelConnections.keptOnAccount') }}</dd>
          <dt>{{ t('account.thisDevice.modelServiceToken') }}</dt>
          <dd>····{{ h.key_last4 }}</dd>
          <dt>{{ t('account.modelConnections.checkedAt') }}</dt>
          <dd>{{ h.checked_at ? relTime(h.checked_at) : t('account.modelConnections.notChecked') }}</dd>
        </dl>
        <p v-if="h.check_error" class="mc-row__sub mc-row__failed">
          {{ t('account.modelConnections.checkFailed', { reason: h.check_error }) }}
        </p>
        <div class="mc-actions">
          <BaseButton kind="ghost" size="sm" :loading="busy === `${h.id}:check`" @click="emit('checkHeld', h.id)">
            {{ t('account.modelConnections.check') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="openRename(h.id, h.name, false)">
            {{ t('account.modelConnections.rename') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" :loading="busy === `${h.id}:revoke`" @click="removingHeld = h">
            {{ t('account.modelConnections.remove') }}
          </BaseButton>
        </div>
      </div>

      <div v-for="c in connections" :key="c.device_id" class="mc-row" :data-connection="c.device_id">
        <div class="mc-row__head">
          <div class="mc-row__id">
            <div class="mc-row__name">{{ c.name || kindLabel(c) }}</div>
            <div v-if="c.name" class="mc-row__sub">{{ kindLabel(c) }}</div>
            <div v-if="c.service_host" class="mc-row__sub">
              {{ t('account.modelConnections.serviceHost', { host: c.service_host }) }}
            </div>
          </div>
          <span class="mc-state" :class="c.revoking ? 'mc-state--pending' : 'mc-state--ok'">
            <span class="mc-state__dot" aria-hidden="true" />{{
              c.revoking ? t('account.modelConnections.revoking') : t('account.modelConnections.ready')
            }}
          </span>
        </div>
        <dl class="mc-spec">
          <dt>{{ t('account.modelConnections.storedOn') }}</dt>
          <dd>
            {{ c.device_name }} ·
            <span :class="c.online ? 'c-ok' : 'mc-row__muted'">{{
              c.online ? t('account.devices.online') : t('account.devices.offline')
            }}</span>
          </dd>
          <dt>{{ t('account.modelConnections.checkedAt') }}</dt>
          <dd>{{ relTime(c.checked_at) }}</dd>
          <dt>{{ t('account.modelConnections.lastUsed') }}</dt>
          <dd>{{ c.last_used_at ? relTime(c.last_used_at) : t('account.modelConnections.neverUsed') }}</dd>
        </dl>
        <p v-if="c.revoking" class="mc-row__sub">
          {{ t('account.modelConnections.revokingHint', { device: c.device_name }) }}
        </p>
        <div v-else class="mc-actions">
          <BaseButton
            kind="ghost"
            size="sm"
            :disabled="!c.online"
            :title="c.online ? undefined : t('account.modelConnections.offlineHint')"
            :loading="busy === `${c.device_id}:check`"
            @click="emit('recheck', c.device_id)"
          >
            {{ t('account.modelConnections.recheck') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="openRename(c.device_id, c.name, true)">
            {{ t('account.modelConnections.rename') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" :loading="busy === `${c.device_id}:revoke`" @click="removing = c">
            {{ t('account.modelConnections.remove') }}
          </BaseButton>
        </div>
      </div>
    </section>

    <ConfirmDialog
      :model-value="!!removing"
      :title="t('account.modelConnections.removeTitle')"
      :confirm-label="t('account.modelConnections.remove')"
      danger
      @update:model-value="removing = null"
      @confirm="confirmRemove"
    >
      {{ t('account.modelConnections.removeBody', { device: removing?.device_name ?? '' }) }}
    </ConfirmDialog>

    <ConfirmDialog
      :model-value="!!removingHeld"
      :title="t('account.modelConnections.removeTitle')"
      :confirm-label="t('account.modelConnections.remove')"
      danger
      @update:model-value="removingHeld = null"
      @confirm="confirmRemoveHeld"
    >
      {{ t('account.modelConnections.removeHeldBody') }}
    </ConfirmDialog>

    <AdaptiveDialog
      :model-value="!!addingKind"
      :title="
        addingKind === 'api_key'
          ? t('account.modelConnections.addApiKey')
          : t('account.modelConnections.addAccountService')
      "
      :primary-label="t('global.save')"
      :primary-loading="busy === 'add'"
      :primary-disabled="!heldComplete"
      :close-disabled="busy === 'add'"
      persistent
      @update:model-value="addingKind = null"
      @primary="saveHeld"
    >
      <template v-if="addingKind">
        <v-text-field
          v-model="heldForm.name"
          autocomplete="off"
          :label="t('account.modelConnections.name')"
          :hint="t('account.modelConnections.nameHint')"
          persistent-hint
        />
        <template v-if="addingKind === 'model_service'">
          <v-text-field
            v-model="heldForm.baseUrl"
            autocomplete="off"
            placeholder="https://"
            :label="t('account.thisDevice.modelServiceUrl')"
          />
          <v-text-field
            v-model="heldForm.model"
            autocomplete="off"
            :label="t('account.thisDevice.modelServiceModel')"
          />
        </template>
        <v-text-field
          v-model="heldForm.key"
          autocomplete="new-password"
          type="password"
          :label="
            addingKind === 'api_key'
              ? t('account.modelConnections.apiKeyLabel')
              : t('account.thisDevice.modelServiceToken')
          "
        />
        <p class="mc-row__sub">
          {{
            addingKind === 'api_key'
              ? t('account.modelConnections.apiKeyHint')
              : t('account.modelConnections.accountServiceHint')
          }}
        </p>
      </template>
    </AdaptiveDialog>

    <AdaptiveDialog
      :model-value="!!renaming"
      :title="t('account.modelConnections.renameTitle')"
      :primary-label="t('global.save')"
      :primary-loading="busy === `${renaming?.id}:rename`"
      persistent
      @update:model-value="renaming = null"
      @primary="saveRename"
    >
      <v-text-field
        v-if="renaming"
        v-model="renameText"
        autocomplete="off"
        maxlength="64"
        :label="t('account.modelConnections.name')"
        :hint="t('account.modelConnections.nameHint')"
        persistent-hint
      />
    </AdaptiveDialog>

    <AdaptiveDialog
      :model-value="adding"
      :title="t('account.modelConnections.addService')"
      :primary-label="t('account.thisDevice.modelServiceSave')"
      :primary-loading="busy === 'service'"
      :primary-disabled="!complete"
      :close-disabled="busy === 'service'"
      persistent
      @update:model-value="adding = false"
      @primary="save"
    >
      <template v-if="adding">
        <p v-if="!devices.length" class="mc-row__sub">{{ t('account.modelConnections.noDevice') }}</p>
        <template v-else>
          <v-select
            v-model="form.deviceId"
            autocomplete="off"
            :items="devices"
            item-title="name"
            item-value="device_id"
            :label="t('account.modelConnections.device')"
          />
          <v-text-field
            v-model="form.baseUrl"
            autocomplete="off"
            placeholder="https://"
            :label="t('account.thisDevice.modelServiceUrl')"
          />
          <v-text-field
            v-model="form.token"
            autocomplete="new-password"
            type="password"
            :label="t('account.thisDevice.modelServiceToken')"
          />
          <v-text-field v-model="form.model" autocomplete="off" :label="t('account.thisDevice.modelServiceModel')" />
          <p class="mc-row__sub">{{ t('account.modelConnections.serviceHint') }}</p>
        </template>
      </template>
    </AdaptiveDialog>
  </div>
</template>

<script setup lang="ts">
import type {
  AccountConnection,
  AccountConnectionInput,
  AccountConnectionKind,
  ModelConnection,
  ModelServiceDevice,
} from '@/types/modelConnections'
import type { ModelServiceInput } from '@/types/ownAgents'

import { computed, reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { connectionKind } from '@/types/modelConnections'

defineOptions({ name: 'ModelConnectionsView' })

const props = defineProps<{
  loading: boolean
  error: string | null
  connections: ModelConnection[]
  /** 保存在账号里的 API key 和模型服务。 */
  held: AccountConnection[]
  /** 能改用自定义模型服务的设备：自己接入、此刻在线的。 */
  devices: ModelServiceDevice[]
  /** 正在进行的动作：`<id>:check`、`<id>:revoke`、`<id>:rename`（id 是账号里那一份的，或设备的）、
   *  `service` 或 `add`。 */
  busy: string
  /** 表单保存成功了几次；变了就关上开着的表单。 */
  saved: number
}>()

const emit = defineEmits<{
  recheck: [deviceId: string]
  revoke: [deviceId: string]
  service: [deviceId: string, service: ModelServiceInput]
  addHeld: [input: AccountConnectionInput]
  checkHeld: [id: string]
  renameHeld: [id: string, name: string]
  renameDevice: [deviceId: string, name: string]
  removeHeld: [id: string]
}>()

function heldLabel(h: AccountConnection): string {
  return h.kind === 'model_service'
    ? t('account.modelConnections.modelService', { model: h.model ?? '' })
    : t('account.modelConnections.apiKey')
}

const removingHeld = ref<AccountConnection | null>(null)
function confirmRemoveHeld() {
  if (removingHeld.value) emit('removeHeld', removingHeld.value.id)
  removingHeld.value = null
}

const addingKind = ref<AccountConnectionKind | null>(null)
const heldForm = reactive({ key: '', name: '', baseUrl: '', model: '' })

function openHeld(kind: AccountConnectionKind) {
  Object.assign(heldForm, { key: '', name: '', baseUrl: '', model: '' })
  addingKind.value = kind
}

// 和服务器保存前查的一样：密钥要有；模型服务的地址是 https 网址，模型名是一个词。
const heldComplete = computed(
  () =>
    heldForm.key.trim() !== '' &&
    (addingKind.value !== 'model_service' ||
      (/^https:\/\/[^\s/]+/.test(heldForm.baseUrl.trim()) && /^\S+$/.test(heldForm.model.trim())))
)

function saveHeld() {
  if (!addingKind.value || !heldComplete.value) return
  emit('addHeld', {
    kind: addingKind.value,
    key: heldForm.key.trim(),
    name: heldForm.name.trim(),
    baseUrl: heldForm.baseUrl.trim(),
    model: heldForm.model.trim(),
  })
}

// 改名的是账号里的一份（id 是它的），或设备上的登录（id 是那台设备）。
const renaming = ref<{ id: string; onDevice: boolean } | null>(null)
const renameText = ref('')

function openRename(id: string, name: string | null, onDevice: boolean) {
  renameText.value = name ?? ''
  renaming.value = { id, onDevice }
}

function saveRename() {
  if (!renaming.value) return
  const name = renameText.value.trim()
  if (renaming.value.onDevice) emit('renameDevice', renaming.value.id, name)
  else emit('renameHeld', renaming.value.id, name)
}

// 订阅档位按厂商的写法首字母大写（max → Max）。
function kindLabel(c: ModelConnection): string {
  switch (connectionKind(c)) {
    case 'modelService':
      return t('account.modelConnections.modelService', { model: c.model ?? '' })
    case 'subscription': {
      const plan = c.subscription_type ?? ''
      return t('account.modelConnections.subscription', { plan: plan.charAt(0).toUpperCase() + plan.slice(1) })
    }
    default:
      return t('account.modelConnections.apiKey')
  }
}

const removing = ref<ModelConnection | null>(null)
function confirmRemove() {
  if (removing.value) emit('revoke', removing.value.device_id)
  removing.value = null
}

const adding = ref(false)
const form = reactive({ deviceId: '', baseUrl: '', token: '', model: '' })

function openService() {
  Object.assign(form, { deviceId: props.devices[0]?.device_id ?? '', baseUrl: '', token: '', model: '' })
  adding.value = true
}

// 地址要是 http(s) 网址，密钥和模型名都要有，模型名是一个词：和设备保存前查的一样。
const complete = computed(
  () =>
    props.devices.some((d) => d.device_id === form.deviceId) &&
    /^https?:\/\/[^\s/]+/.test(form.baseUrl.trim()) &&
    form.token.trim() !== '' &&
    /^\S+$/.test(form.model.trim())
)

function save() {
  if (!complete.value) return
  emit('service', form.deviceId, {
    baseUrl: form.baseUrl.trim(),
    token: form.token.trim(),
    model: form.model.trim(),
  })
}

// 保存成功才关上表单，并清掉填过的密钥；失败时表单留着，填的东西都还在。
watch(
  () => props.saved,
  () => {
    adding.value = false
    form.token = ''
    addingKind.value = null
    heldForm.key = ''
    renaming.value = null
  }
)
</script>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.mc__notice {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
}

.mc-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 24px 16px;
  border-top: 1px solid var(--line);
}

.mc-row__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.mc-row__id {
  flex: 1;
  min-width: 0;
}

.mc-row__name {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.mc-row__sub {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}

.mc-row__muted {
  color: var(--muted);
}

.mc-state {
  display: inline-flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
}

.mc-state__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
}

.mc-state--ok {
  color: var(--ok-ink);
}

.mc-state--ok .mc-state__dot {
  background: var(--ok);
}

.mc-state--pending {
  color: var(--muted);
}

.mc-state--failed {
  color: var(--danger-ink);
}

.mc-state--failed .mc-state__dot {
  background: var(--danger);
}

.mc-row__failed {
  color: var(--danger-ink);
}

.mc-head {
  flex-wrap: wrap;
  gap: 8px;
}

.mc-state--pending .mc-state__dot {
  background: var(--faint);
}

.mc-spec {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 12px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.mc-spec dt {
  color: var(--faint);
}

.mc-spec dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.mc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`），和 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .mc-row {
    padding: 12px 16px 14px;
  }
}
</style>
