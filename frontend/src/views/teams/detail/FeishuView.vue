<script setup lang="ts">
// 团队设置「飞书应用」那一栏的画面：这个团队连接成员飞书时用的企业自建应用，和本人自己
// 通过它的连接。
//
// 企业自建应用只在它自己那家企业的飞书里能用，所以应用设在团队上；一个人自己用就设在个人
// 团队上（这一栏在个人设置里）。每个成员都看得到团队用的是哪个应用，也在这里连接、重新
// 连接、断开自己的飞书账号（授权是本人自己的）。能改团队设置的人另外填 App ID / Secret /
// 版本，下面写着在飞书里建这个应用的几步，重定向 URL、权限配置、申请理由都能复制。Secret
// 只写不回显：已经设过时空着保存表示不改它。取数、保存、连接、复制都在容器 `Feishu.vue`
// 里，这一半只吃 props、往上报。
import type { FeishuConnection, TeamFeishuApp } from '@/api/feishu'

import { computed, reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 个人团队：这一栏在个人设置里，说法换成「你自己」。 */
  personal: boolean
  /** 读到的那一份；还没读到是 `null`。 */
  app: TeamFeishuApp | null
  loading: boolean
  /** 读失败时那句原话；`''` 表示没失败。 */
  loadError: string
  /** 能不能改：团队管理员，或个人团队的主人。 */
  canManage: boolean
  /** 本人自己通过这个应用连的飞书；没连是 `null`。 */
  connection: FeishuConnection | null
  /** 连接 / 重新连接 / 断开 正在进行。 */
  connecting: boolean
}>()

const emit = defineEmits<{
  /** 表单拼好了；`done` 带回成没成、以及没成时那句话。 */
  save: [body: { app_id: string; app_secret: string; domain: string }, done: (ok: boolean, message?: string) => void]
  /** 删掉这个应用。用它的连接下一次调用会说应用已移除、请重新连接。 */
  remove: []
  copy: [text: string]
  retry: []
  /** 用这个应用连接本人的飞书账号：建好那一行就跳去飞书授权。 */
  connect: []
  /** 已有的那一行再授权一次。 */
  reconnect: [connection: FeishuConnection]
  /** 断开本人的这个飞书连接。 */
  disconnect: [connection: FeishuConnection]
}>()

/** 粘进飞书开发者后台「权限管理 → 批量导入/导出权限」的那份 JSON：只要用户身份权限，
 *  和后端 `feishu.USER_SCOPES` 一一对应，改一处就改另一处。 */
const FEISHU_SCOPES_IMPORT = JSON.stringify(
  { scopes: { tenant: [], user: ['offline_access', 'search:docs:read', 'docx:document'] } },
  null,
  2
)

const form = reactive({ domain: 'feishu', app_id: '', app_secret: '' })
const saving = ref(false)
const saveError = ref('')
const removing = ref(false)
const disconnecting = ref(false)
watch(
  () => props.app,
  (app) => {
    if (!app) return
    form.domain = app.domain || 'feishu'
    form.app_id = app.app_id
    form.app_secret = ''
  },
  { immediate: true }
)
const consoleUrl = computed(() =>
  form.domain === 'lark' ? 'https://open.larksuite.com/app' : 'https://open.feishu.cn/app'
)
const configured = computed(() => !!props.app?.configured)
const appLine = computed(() =>
  configured.value
    ? t(props.personal ? 'integrations.team.usesPersonal' : 'integrations.team.uses', {
        appId: props.app?.app_id ?? '',
        domain: props.app?.domain ?? '',
      })
    : t('integrations.team.notConfigured')
)
/** 本人这一个连接说到哪一步：没连、授权没完成、授权失效、已连接。 */
const mine = computed(() => {
  const c = props.connection
  if (!c) return 'none'
  if (c.status === 'auth_failed') return 'lapsed'
  return c.user_authorized ? 'connected' : 'pending'
})

function save() {
  if (saving.value) return
  saving.value = true
  saveError.value = ''
  emit('save', { ...form }, (ok: boolean, message?: string) => {
    saving.value = false
    if (ok) form.app_secret = ''
    else saveError.value = message ?? ''
  })
}

function confirmDisconnect() {
  disconnecting.value = false
  if (props.connection) emit('disconnect', props.connection)
}

function confirmRemove() {
  removing.value = false
  emit('remove')
}
</script>

<template>
  <div class="settings-page">
    <header class="settings-page__head">
      <h1 class="t-page-title">{{ t('integrations.team.title') }}</h1>
      <p class="settings-page__lede">
        {{ personal ? t('integrations.team.ledePersonal') : t('integrations.team.lede') }}
      </p>
    </header>

    <p v-if="loadError" role="alert" class="t-body c-danger">
      {{ loadError }}
      <BaseButton kind="ghost" size="sm" @click="emit('retry')">{{ t('integrations.team.retry') }}</BaseButton>
    </p>

    <section v-else class="settings-card" :aria-busy="loading">
      <div class="settings-card__title">{{ t('integrations.team.appTitle') }}</div>
      <p class="settings-card__desc feishu-status">{{ appLine }}</p>
      <p v-if="!configured && !canManage" class="settings-card__desc">{{ t('integrations.team.adminSets') }}</p>
      <form v-if="canManage" class="feishu-form" @submit.prevent="save">
        <v-select
          v-model="form.domain"
          autocomplete="off"
          :items="[
            { title: t('integrations.team.feishu'), value: 'feishu' },
            { title: 'Lark', value: 'lark' },
          ]"
          :label="t('integrations.team.domain')"
        />
        <v-text-field v-model="form.app_id" autocomplete="off" :label="t('integrations.team.appId')" />
        <v-text-field
          v-model="form.app_secret"
          autocomplete="new-password"
          type="password"
          :label="t('integrations.team.appSecret')"
          :hint="app?.configured ? t('integrations.team.secretHint') : ''"
          persistent-hint
        />
        <p v-if="saveError" role="alert" class="t-body c-danger">{{ saveError }}</p>
        <div class="feishu-actions">
          <BaseButton v-if="app?.configured" kind="ghost" size="sm" @click="removing = true">
            {{ t('integrations.team.remove') }}
          </BaseButton>
          <BaseButton kind="primary" size="sm" :loading="saving" @click="save">
            {{ t('integrations.team.save') }}
          </BaseButton>
        </div>
      </form>
    </section>

    <section v-if="configured && !loadError" class="settings-card" data-mine>
      <div class="settings-card__title">{{ t('integrations.team.mineTitle') }}</div>
      <div class="feishu-mine">
        <p class="t-body feishu-mine__state" :class="{ 'c-danger': mine === 'lapsed' }">
          {{ t(`integrations.team.mine.${mine}`) }}
        </p>
        <div class="feishu-actions">
          <BaseButton
            v-if="connection && mine !== 'none'"
            kind="ghost"
            size="sm"
            :disabled="connecting"
            @click="disconnecting = true"
          >
            {{ t('integrations.team.disconnect') }}
          </BaseButton>
          <BaseButton v-if="mine === 'none'" kind="primary" size="sm" :loading="connecting" @click="emit('connect')">
            {{ t('integrations.team.connect') }}
          </BaseButton>
          <BaseButton
            v-else-if="connection"
            :kind="mine === 'connected' ? 'secondary' : 'primary'"
            size="sm"
            :loading="connecting"
            @click="emit('reconnect', connection)"
          >
            {{ mine === 'pending' ? t('integrations.team.connect') : t('integrations.team.reconnect') }}
          </BaseButton>
        </div>
      </div>
    </section>

    <section v-if="canManage && !loadError" class="settings-card">
      <div class="settings-card__title">{{ t('integrations.team.setupTitle') }}</div>
      <ol class="feishu-steps t-body">
        <li>{{ t('integrations.team.step1', { console: consoleUrl }) }}</li>
        <li>
          {{ t('integrations.team.step2') }}
          <!-- One read-only line plus Copy, the way the team join link is drawn: the
               address never wraps into what looks like two values. -->
          <div v-if="app?.redirect_uri" class="feishu-link">
            <v-text-field
              autocomplete="off"
              :model-value="app.redirect_uri"
              :aria-label="t('integrations.team.redirectLabel')"
              readonly
              hide-details
              density="compact"
              variant="outlined"
              class="feishu-link__field"
            />
            <BaseButton kind="secondary" size="sm" @click="emit('copy', app.redirect_uri)">
              {{ t('integrations.team.copy') }}
            </BaseButton>
          </div>
        </li>
        <li>
          {{ t('integrations.team.step3') }}
          <div class="feishu-copy">
            <pre class="feishu-code">{{ FEISHU_SCOPES_IMPORT }}</pre>
            <BaseButton kind="secondary" size="sm" @click="emit('copy', FEISHU_SCOPES_IMPORT)">
              {{ t('integrations.team.copy') }}
            </BaseButton>
          </div>
        </li>
        <li>
          {{ personal ? t('integrations.team.step4Personal') : t('integrations.team.step4') }}
          <div class="feishu-copy">
            <pre class="feishu-code">{{ t('integrations.team.reason') }}</pre>
            <BaseButton kind="secondary" size="sm" @click="emit('copy', t('integrations.team.reason'))">
              {{ t('integrations.team.copy') }}
            </BaseButton>
          </div>
        </li>
        <li>{{ t('integrations.team.step5') }}</li>
      </ol>
    </section>

    <ConfirmDialog
      :model-value="disconnecting"
      :title="t('integrations.team.disconnectTitle')"
      :confirm-label="t('integrations.team.disconnect')"
      danger
      @update:model-value="disconnecting = false"
      @confirm="confirmDisconnect"
    >
      {{ t('integrations.team.disconnectBody') }}
    </ConfirmDialog>

    <ConfirmDialog
      :model-value="removing"
      :title="t('integrations.team.removeTitle')"
      :confirm-label="t('integrations.team.remove')"
      danger
      @update:model-value="removing = false"
      @confirm="confirmRemove"
    >
      {{ t('integrations.team.removeBody') }}
    </ConfirmDialog>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.feishu-status {
  margin-top: 4px;
}

.feishu-mine {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px 16px;
}

.feishu-mine__state {
  margin: 0;
  color: var(--text);
}

.feishu-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 24px 16px;
}

.feishu-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.feishu-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 8px 24px 16px 44px;
  color: var(--text);
}

.feishu-link,
.feishu-copy {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 4px;
}

.feishu-link {
  align-items: center;
}

.feishu-link__field {
  flex: 1 1 auto;
  min-width: 0;
}

.feishu-link__field :deep(input) {
  font-family: var(--font-mono);
  font-size: 12px;
}

.feishu-code {
  flex: 1;
  min-width: 0;
  margin: 0;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 767.98px) {
  .feishu-form,
  .feishu-mine {
    padding: 8px 16px 14px;
  }

  .feishu-steps {
    padding: 8px 16px 14px 36px;
  }
}
</style>
