<template>
  <div class="mc-page settings-page">
    <header class="settings-page__head">
      <div class="mc-lede">
        <h1 class="t-page-title">{{ t('home.nav.teamModels') }}</h1>
        <p class="settings-page__lede">{{ t('account.modelConnections.lede') }}</p>
        <p v-if="!canManage" class="settings-page__lede">{{ t('account.modelConnections.readOnly') }}</p>
      </div>
      <div class="settings-page__actions">
        <BaseButton prepend-icon="mdi-refresh" :loading="loading" @click="emit('refresh')">
          {{ t('account.modelConnections.recheck') }}
        </BaseButton>
        <BaseButton kind="secondary" prepend-icon="mdi-plus" @click="addingPersonal = true">
          {{ t('account.modelConnections.addPersonal') }}
        </BaseButton>
      </div>
    </header>

    <p v-if="error" role="alert" class="mc__notice c-danger">{{ error }}</p>

    <section class="settings-card" :aria-label="t('account.modelConnections.platformTitle')">
      <div class="settings-card__title">{{ t('account.modelConnections.platformTitle') }}</div>
      <p class="settings-card__desc">{{ t('account.modelConnections.platformHint') }}</p>
      <ul class="mc-platform">
        <li v-for="model in platformList" :key="model.id">
          <span class="mc-platform__name">{{ model.name }}</span>
          <span v-if="model.note" class="mc-platform__note">{{ model.note }}</span>
        </li>
      </ul>
    </section>
    <section class="settings-card" :aria-label="t('account.modelConnections.devicesTitle')">
      <div class="settings-card__head mc-head">
        <div class="settings-card__title">{{ t('account.modelConnections.devicesTitle') }}</div>
        <div v-if="canManage" class="mc-headbtns">
          <!-- The visible text is the short form; the accessible name keeps the full action. -->
          <BaseButton
            kind="ghost"
            size="sm"
            prepend-icon="mdi-plus"
            :aria-label="t('account.modelConnections.addSubscription')"
            @click="emit('login')"
          >
            {{ t('account.modelConnections.addSubscription') }}
          </BaseButton>
        </div>
      </div>

      <p class="settings-card__desc">{{ t('account.modelConnections.loginHint') }}</p>
      <p v-if="loginCommand" class="settings-card__desc mc-command">
        {{ t('account.modelConnections.loginTerminal') }}<code>{{ loginCommand }}</code>
      </p>

      <div v-if="firstLoad" class="d-flex justify-center py-6">
        <v-progress-circular indeterminate size="24" />
      </div>
      <BaseEmptyState
        v-else-if="!connections.length"
        size="inline"
        class="settings-empty"
        :title="t('account.modelConnections.devicesEmpty')"
      />

      <div
        v-for="c in connections"
        :key="c.id"
        class="mc-item"
        :class="{ 'mc-item--open': isOpen(c.id) }"
        :data-connection="c.id"
      >
        <button
          type="button"
          class="mc-sum"
          :aria-expanded="isOpen(c.id)"
          :aria-controls="isOpen(c.id) ? `${uid}-${c.id}` : undefined"
          @click="toggle(c.id)"
        >
          <v-icon class="mc-sum__chev" icon="mdi-chevron-right" size="18" aria-hidden="true" />
          <span class="mc-sum__name">{{ c.name || kindLabel(c) }}</span>
          <span v-if="c.owner_name" class="mc-sum__kind">{{ c.owner_name }}</span>
          <span v-if="c.name" class="mc-sum__kind">{{ kindLabel(c) }}</span>
          <span v-if="c.account_email" class="mc-sum__kind">{{ c.account_email }}</span>
          <span class="mc-sum__where">
            <span class="mc-dot" :class="{ 'mc-dot--on': c.online }" aria-hidden="true" />
            <span class="mc-sum__device">{{ c.device_name }}</span>
          </span>
          <!-- A subscription is used on the computer that holds it, so it is ready only while that computer is online. -->
          <span class="mc-state" :class="c.revoking || !c.online ? 'mc-state--pending' : 'mc-state--ok'">{{
            c.revoking
              ? t('account.modelConnections.revoking')
              : c.online
                ? t('account.modelConnections.ready')
                : t('account.devices.offline')
          }}</span>
        </button>
        <Transition name="mc-body">
          <div v-if="isOpen(c.id)" :id="`${uid}-${c.id}`" class="mc-body">
            <div class="mc-body__inner">
              <div class="mc-body__pad">
                <p v-if="c.owner_name" class="mc-note">{{ t('account.modelConnections.personalHint') }}</p>
                <dl class="mc-spec">
                  <template v-if="c.owner_name"
                    ><dt>{{ t('account.modelConnections.owner') }}</dt>
                    <dd>{{ c.owner_name }}</dd></template
                  >
                  <template v-if="c.account_email"
                    ><dt>{{ t('account.modelConnections.loginAccount') }}</dt>
                    <dd>{{ [c.account_email, c.account_org].filter(Boolean).join(' · ') }}</dd></template
                  >
                  <dt>{{ t('account.modelConnections.storedOn') }}</dt>
                  <dd>
                    {{ c.device_name }} ·
                    <span :class="c.online ? 'c-ok' : 'mc-muted'">{{
                      c.online ? t('account.devices.online') : t('account.devices.offline')
                    }}</span>
                  </dd>
                  <!-- Whoever can edit the models reads them in the field below instead. -->
                  <template v-if="!(c.can_manage && !c.revoking)"
                    ><dt>{{ t('account.modelConnections.models') }}</dt>
                    <dd>{{ c.allowed_models.join('、') }}</dd></template
                  >
                  <dt>{{ t('account.modelConnections.checkedAt') }}</dt>
                  <dd>{{ relTime(c.checked_at) }}</dd>
                  <dt>{{ t('account.modelConnections.lastUsed') }}</dt>
                  <dd>{{ c.last_used_at ? relTime(c.last_used_at) : t('account.modelConnections.neverUsed') }}</dd>
                </dl>
                <p v-if="c.revoking" class="mc-note">
                  {{ t('account.modelConnections.revokingHint', { device: c.device_name }) }}
                </p>
                <div v-if="c.can_manage && !c.revoking" class="mc-models">
                  <v-combobox
                    v-model="modelDraft[c.id]"
                    autocomplete="off"
                    :items="c.allowed_models"
                    multiple
                    chips
                    density="compact"
                    hide-details
                    :label="t('account.modelConnections.models')"
                  />
                  <BaseButton
                    kind="secondary"
                    size="sm"
                    :loading="busy === `${c.id}:models`"
                    @click="emit('setModels', c.id, modelDraft[c.id] ?? [], true)"
                    >{{ t('account.modelConnections.saveModels') }}</BaseButton
                  >
                </div>
                <div v-if="(c.can_manage && !c.revoking) || c.can_remove" class="mc-actions">
                  <BaseButton
                    v-if="c.can_manage && !c.revoking"
                    kind="secondary"
                    size="sm"
                    :disabled="!c.online"
                    :title="c.online ? undefined : t('account.modelConnections.offlineHint')"
                    :loading="busy === `${c.id}:check`"
                    @click="emit('recheck', c.id)"
                  >
                    {{ t('account.modelConnections.recheck') }}
                  </BaseButton>
                  <template v-if="c.can_manage && !c.revoking">
                    <BaseButton kind="ghost" size="sm" @click="openRename(c.id, c.name, true)">
                      {{ t('account.modelConnections.rename') }}
                    </BaseButton>
                    <BaseButton kind="ghost" size="sm" :loading="busy === `${c.id}:revoke`" @click="removing = c">
                      {{ t('account.modelConnections.remove') }}
                    </BaseButton>
                  </template>
                  <BaseButton v-if="c.can_remove" kind="ghost" size="sm" @click="emit('removePersonal', c.id)">{{
                    t('account.modelConnections.removePersonal')
                  }}</BaseButton>
                </div>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </section>

    <section class="settings-card" :aria-label="t('account.modelConnections.heldTitle')">
      <div class="settings-card__head mc-head">
        <div class="settings-card__title">{{ t('account.modelConnections.heldTitle') }}</div>
        <div v-if="canManage" class="mc-headbtns">
          <BaseButton kind="ghost" size="sm" prepend-icon="mdi-plus" @click="openHeld('api_key')">
            {{ t('account.modelConnections.addApiKey') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" prepend-icon="mdi-plus" @click="openHeld('model_service')">
            {{ t('account.modelConnections.addAccountService') }}
          </BaseButton>
        </div>
      </div>

      <div v-if="firstLoad" class="d-flex justify-center py-6">
        <v-progress-circular indeterminate size="24" />
      </div>
      <BaseEmptyState
        v-else-if="!held.length"
        size="inline"
        class="settings-empty"
        :title="t('account.modelConnections.heldEmpty')"
      />

      <div
        v-for="h in held"
        :key="h.id"
        class="mc-item"
        :class="{ 'mc-item--open': isOpen(h.id) }"
        :data-connection="h.id"
      >
        <button
          type="button"
          class="mc-sum"
          :aria-expanded="isOpen(h.id)"
          :aria-controls="isOpen(h.id) ? `${uid}-${h.id}` : undefined"
          @click="toggle(h.id)"
        >
          <v-icon class="mc-sum__chev" icon="mdi-chevron-right" size="18" aria-hidden="true" />
          <span class="mc-sum__name">{{ h.name || heldLabel(h) }}</span>
          <span v-if="h.owner_name" class="mc-sum__kind">{{ h.owner_name }}</span>
          <span v-if="h.name" class="mc-sum__kind">{{ heldLabel(h) }}</span>
          <span class="mc-sum__where">····{{ h.key_last4 }}</span>
          <span class="mc-state" :class="h.check_error ? 'mc-state--failed' : 'mc-state--ok'">{{
            h.check_error ? t('account.modelConnections.checkFailedState') : t('account.modelConnections.ready')
          }}</span>
        </button>
        <Transition name="mc-body">
          <div v-if="isOpen(h.id)" :id="`${uid}-${h.id}`" class="mc-body">
            <div class="mc-body__inner">
              <div class="mc-body__pad">
                <p v-if="h.owner_name" class="mc-note">{{ t('account.modelConnections.personalHint') }}</p>
                <p v-if="h.check_error" class="mc-note mc-note--failed">
                  {{ t('account.modelConnections.checkFailed', { reason: h.check_error }) }}
                </p>
                <dl class="mc-spec">
                  <template v-if="h.owner_name"
                    ><dt>{{ t('account.modelConnections.owner') }}</dt>
                    <dd>{{ h.owner_name }}</dd></template
                  >
                  <template v-if="!h.can_manage"
                    ><dt>{{ t('account.modelConnections.models') }}</dt>
                    <dd>{{ h.allowed_models.join('、') }}</dd></template
                  >
                  <dt>{{ t('account.modelConnections.storedOn') }}</dt>
                  <dd>{{ t('account.modelConnections.keptOnAccount') }}</dd>
                  <template v-if="h.service_host">
                    <dt>{{ t('account.thisDevice.modelServiceUrl') }}</dt>
                    <dd>{{ h.service_host }}</dd>
                  </template>
                  <dt>{{ t('account.thisDevice.modelServiceToken') }}</dt>
                  <dd>····{{ h.key_last4 }}</dd>
                  <dt>{{ t('account.modelConnections.checkedAt') }}</dt>
                  <dd>{{ h.checked_at ? relTime(h.checked_at) : t('account.modelConnections.notChecked') }}</dd>
                </dl>
                <div v-if="h.can_manage" class="mc-models">
                  <v-combobox
                    v-model="modelDraft[h.id]"
                    autocomplete="off"
                    :items="h.allowed_models"
                    multiple
                    chips
                    density="compact"
                    hide-details
                    :label="t('account.modelConnections.models')"
                  />
                  <BaseButton
                    kind="secondary"
                    size="sm"
                    :loading="busy === `${h.id}:models`"
                    @click="emit('setModels', h.id, modelDraft[h.id] ?? [], false)"
                    >{{ t('account.modelConnections.saveModels') }}</BaseButton
                  >
                </div>
                <div v-if="h.can_manage || h.can_remove" class="mc-actions">
                  <template v-if="h.can_manage">
                    <BaseButton
                      kind="secondary"
                      size="sm"
                      :loading="busy === `${h.id}:check`"
                      @click="emit('checkHeld', h.id)"
                    >
                      {{ t('account.modelConnections.check') }}
                    </BaseButton>
                    <BaseButton kind="ghost" size="sm" @click="openRename(h.id, h.name, false)">
                      {{ t('account.modelConnections.rename') }}
                    </BaseButton>
                    <BaseButton kind="ghost" size="sm" :loading="busy === `${h.id}:revoke`" @click="removingHeld = h">
                      {{ t('account.modelConnections.remove') }}
                    </BaseButton>
                  </template>
                  <BaseButton v-if="h.can_remove" kind="ghost" size="sm" @click="emit('removePersonal', h.id)">{{
                    t('account.modelConnections.removePersonal')
                  }}</BaseButton>
                </div>
              </div>
            </div>
          </div>
        </Transition>
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
        <p class="mc-note">
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
      :model-value="addingPersonal"
      :title="t('account.modelConnections.addPersonal')"
      :primary-label="t('account.modelConnections.addPersonal')"
      :primary-disabled="!personalId"
      :primary-loading="busy === 'personal'"
      @update:model-value="addingPersonal = false"
      @primary="emit('addPersonal', personalId)"
    >
      <v-select
        v-model="personalId"
        autocomplete="off"
        :items="availablePersonal"
        item-title="name"
        item-value="id"
        :label="t('account.modelConnections.connection')"
      />
      <p class="mc-note">{{ t('account.modelConnections.personalHint') }}</p>
    </AdaptiveDialog>
  </div>
</template>

<script setup lang="ts">
import type { PersonalConnectionChoice } from '@/api/modelConnections'
import type {
  AccountConnection,
  AccountConnectionInput,
  AccountConnectionKind,
  ModelConnection,
} from '@/types/modelConnections'

import { computed, reactive, ref, useId, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

defineOptions({ name: 'ModelsView' })

const props = withDefaults(
  defineProps<{
    canManage?: boolean
    platformModels?: { id: string; label: string }[]
    loginCommand?: string | null
    loading: boolean
    error: string | null
    connections: ModelConnection[]
    /** 保存在账号里的 API key 和模型服务。 */
    held: AccountConnection[]
    /** Existing credentials owned by the current member. */
    personalChoices?: PersonalConnectionChoice[]
    /** 正在进行的动作：`<id>:check`、`<id>:revoke`、`<id>:rename`（id 是账号里那一份的，或设备的）、
     *  `service` 或 `add`。 */
    busy: string
    /** 表单保存成功了几次；变了就关上开着的表单。 */
    saved: number
  }>(),
  { canManage: true, platformModels: () => [], loginCommand: null, personalChoices: () => [] }
)

const emit = defineEmits<{
  refresh: []
  recheck: [loginId: string]
  revoke: [loginId: string]
  login: []
  addPersonal: [id: string]
  removePersonal: [id: string]
  setModels: [id: string, models: string[], onDevice: boolean]
  addHeld: [input: AccountConnectionInput]
  checkHeld: [id: string]
  renameHeld: [id: string, name: string]
  renameDevice: [loginId: string, name: string]
  removeHeld: [id: string]
}>()

const uid = useId()
const modelDraft = reactive<Record<string, string[]>>({})
watch(
  () => [props.connections, props.held] as const,
  ([connections, held]) => {
    for (const c of [...connections, ...held]) modelDraft[c.id] = [...(c.allowed_models ?? [])]
  },
  { immediate: true }
)
/** 「添加我的连接」的对话框开没开；打开它的按钮在标题那一行。 */
const addingPersonal = defineModel<boolean>('addingPersonal', { default: false })
const personalId = ref('')
const availablePersonal = computed(() => props.personalChoices.filter((c) => !c.added))
// 网关给的名字可以在末尾括号里带一句备注（「免费·限时」这类），列表里把它画成名字下面的一行灰字。
const platformList = computed(() =>
  props.platformModels.map((m) => {
    const found = /^(.*\S)\s*[（(]([^（）()]+)[）)]$/.exec(m.label)
    return { id: m.id, name: found ? found[1] : m.label, note: found ? found[2] : '' }
  })
)
// 第一次取数还没回来：两张卡里先转圈，不先说「暂无」。
const firstLoad = computed(() => props.loading && !props.connections.length && !props.held.length)

// 展开了哪几份；一份收起时它的详情和按钮都不在页面上。
const opened = ref(new Set<string>())
const isOpen = (id: string) => opened.value.has(id)
function toggle(id: string) {
  const next = new Set(opened.value)
  if (!next.delete(id)) next.add(id)
  opened.value = next
}

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
    allowedModels: addingKind.value === 'model_service' ? [heldForm.model.trim()] : ['opus', 'sonnet', 'haiku'],
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
  const plan = c.subscription_type ?? ''
  return t('account.modelConnections.subscription', { plan: plan.charAt(0).toUpperCase() + plan.slice(1) })
}

const removing = ref<ModelConnection | null>(null)
function confirmRemove() {
  if (removing.value) emit('revoke', removing.value.id)
  removing.value = null
}

// 保存成功才关上表单，并清掉填过的密钥；失败时表单留着，填的东西都还在。
watch(
  () => props.saved,
  () => {
    addingPersonal.value = false
    addingKind.value = null
    heldForm.key = ''
    renaming.value = null
  }
)
</script>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.mc-page {
  display: grid;
  gap: 20px;
}

.mc-lede {
  display: grid;
  gap: 4px;
  max-width: 65ch;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.mc-lede p {
  margin: 0;
}

/* The platform pool is a long list of short names: read across in columns, a
   note the gateway gave a model sits under its name in --muted. */
.mc-platform {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px 24px;
  padding: 4px 24px 20px;
  margin: 0;
  list-style: none;
}

.mc-platform li {
  display: grid;
  min-width: 0;
}

.mc-platform__name {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  overflow-wrap: anywhere;
}

.mc-platform__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.mc-command code {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
  color: var(--text);
}

/* The editable model list and its save button share one line. */
.mc-models {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-top: 8px;
}

.mc-models .v-combobox {
  flex: 1;
  min-width: 0;
}
.mc__notice {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
}

.mc-headbtns {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  margin-top: 16px;
}

.mc-item {
  border-top: 1px solid var(--line);
}

/* The whole summary line is the toggle: one button, so it is one tab stop and
   says expanded / collapsed. */
.mc-sum {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  min-height: 48px;
  padding: 8px 24px 8px 16px;
  font: inherit;
  color: inherit;
  text-align: start;
  cursor: pointer;
  background: none;
  border: 0;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.mc-sum:hover {
  background: var(--fill);
}

.mc-sum:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.mc-sum__chev {
  flex-shrink: 0;
  color: var(--faint);
  transition: transform var(--dur-base) var(--ease-standard);
}

.mc-item--open .mc-sum__chev {
  transform: rotate(90deg);
}

.mc-sum__name {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-sum__kind {
  flex: 0 10 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-sum__where {
  display: inline-flex;
  flex: 0 1 auto;
  gap: 6px;
  align-items: center;
  min-width: 0;
  max-width: 40%;
  margin-left: auto;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.mc-sum__device {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mc-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  background: var(--faint);
  border-radius: var(--radius-pill);
}

.mc-dot--on {
  background: var(--ok);
}

.mc-state {
  flex-shrink: 0;
  min-width: 64px;
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: end;
}

.mc-state--ok {
  color: var(--ok-ink);
}

.mc-state--pending {
  color: var(--muted);
}

.mc-state--failed {
  color: var(--danger-ink);
}

/* Expand / collapse animates the height (§9.2): rows 0fr -> 1fr while the
   details enter, back to 0fr, one step quicker, while they leave. */
.mc-body {
  display: grid;
  grid-template-rows: 1fr;
}

.mc-body-enter-active {
  transition: grid-template-rows var(--dur-base) var(--ease-out);
}

.mc-body-leave-active {
  transition: grid-template-rows var(--dur-quick) var(--ease-in);
}

.mc-body-enter-from,
.mc-body-leave-to {
  grid-template-rows: 0fr;
}

.mc-body__inner {
  min-height: 0;
  overflow: hidden;
}

.mc-body__pad {
  display: grid;
  gap: 12px;
  padding: 0 24px 16px 42px;
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
  color: var(--muted);
}

.mc-spec dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.mc-muted {
  color: var(--muted);
}

.mc-note {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}

.mc-note--failed {
  color: var(--danger-ink);
}

.mc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`），和 `settings-card.css` 同一条线。
   手机上一行放不下四样，先省掉档位；名称和设备名各自省略，状态始终完整。 */
@media (max-width: 767.98px) {
  .mc-sum {
    padding-right: 16px;
  }

  .mc-sum__kind {
    display: none;
  }

  .mc-body__pad {
    padding: 0 16px 16px 42px;
  }

  .mc-platform {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    padding: 4px 16px 16px;
  }

  .mc-models {
    flex-wrap: wrap;
  }

  /* The title keeps its line; add buttons that do not fit beside it go under it. */
  .mc-head {
    flex-wrap: wrap;
    row-gap: 0;
    padding-bottom: 8px;
  }

  .mc-head .settings-card__title {
    padding-bottom: 0;
    white-space: nowrap;
  }

  .mc-headbtns {
    flex-wrap: wrap;
    flex-shrink: 1;
    min-width: 0;
    margin-left: 12px;
  }
}
</style>
