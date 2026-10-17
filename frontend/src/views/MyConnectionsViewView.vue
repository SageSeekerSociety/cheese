<!--
  The rendering half of MyConnectionsView.vue, split out by hand: props in, events out.
  The page keeps the requests, the route and the toasts; the mail form's draft lives here,
  because every field of it is an element in this file.
-->

<script setup lang="ts">
import type { Integration, MailDraft } from '../api'
import type { Project } from '../cx_types'

import { computed, reactive, ref } from 'vue'

import ConfirmDialog from '../components/base/ConfirmDialog.vue'
import AdaptiveDialog from '../components/common/AdaptiveDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

// 我的连接 (/settings/connections) 的画面那一半。这一页是什么、为什么只画这些，写在
// 容器 `MyConnectionsView.vue` 的文件头。
const props = defineProps<{
  integrations: Integration[]
  drafts: MailDraft[]
  /** 可选的项目，两张 `v-select` 的选项。 */
  projects: Project[]
  loading: boolean
  /** 读整页失败时那句原话；`''` 表示没失败。 */
  error: string
  /** 正在飞的那个动作（`<id>:<动作>`），用来只把点过的那颗按钮画成在转。 */
  busy: string
  /** 平台管理员没配飞书应用：按钮灰着，底下写明为什么。 */
  feishuMissing: boolean
  /** 还在问「配没配」：按钮先灰着，不然没配的时候它会先亮一下再变灰。 */
  feishuAsking: boolean
}>()

const emit = defineEmits<{
  refresh: []
  discard: [draft: MailDraft]
  send: [draft: MailDraft]
  'connect-feishu': []
  'set-grants': [row: Integration, grants: string[]]
  recheck: [row: Integration]
  authorize: [row: Integration]
  remove: [row: Integration]
  /** 表单已经按服务端认的样子拼好了；`done` 带回成没成、以及没成时那句话。 */
  save: [body: Record<string, unknown>, done: (ok: boolean, message?: string) => void]
}>()

const pending = computed(() => props.drafts.filter((d) => d.status === 'drafted'))

const statusLabel = (status: Integration['status']) => t(`account.connections.status.${status}`)

const PRESETS = [
  {
    title: t('account.connections.preset.qq'),
    imap: 'imap.qq.com',
    smtp: 'smtp.qq.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: t('account.connections.preset.163'),
    imap: 'imap.163.com',
    smtp: 'smtp.163.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: t('account.connections.preset.exmail'),
    imap: 'imap.exmail.qq.com',
    smtp: 'smtp.exmail.qq.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: t('account.connections.preset.aliyun'),
    imap: 'imap.qiye.aliyun.com',
    smtp: 'smtp.qiye.aliyun.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: t('account.connections.preset.gmail'),
    imap: 'imap.gmail.com',
    smtp: 'smtp.gmail.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: 'Outlook / Microsoft 365',
    imap: 'outlook.office365.com',
    smtp: 'smtp.office365.com',
    smtpPort: 587,
    security: 'starttls',
  },
  { title: t('account.connections.preset.other'), imap: '', smtp: '', smtpPort: 465, security: 'ssl' },
]

const attachmentList = (d: MailDraft) =>
  d.attachments.length
    ? d.attachments.map((a) => t('account.connections.attachment', { name: a.name, size: a.size })).join('、')
    : t('account.connections.none')

const projectName = (id: string) => props.projects.find((p) => p.id === id)?.name ?? id

// ── 两个确认框 ─────────────────────────────────────────────────────────────
// 开着的那一条就是这一页要问的事，问完立刻关掉再把动作交出去（请求在页面里）。
const confirming = ref<MailDraft | null>(null)
const removing = ref<Integration | null>(null)

function confirmSend() {
  const draft = confirming.value
  confirming.value = null
  if (draft) emit('send', draft)
}

function confirmRemove() {
  const row = removing.value
  removing.value = null
  if (row) emit('remove', row)
}

// ── 接入 ───────────────────────────────────────────────────────────────────
// 只剩邮箱这一张表单。飞书不再有「填 App ID / Secret」这一种接入方式 —— 它是成员点
// 一下「连接飞书」、跳到飞书授权页那一步（页面里的 `connectFeishuAccount`），凭据在
// 管理员那边。表单自己那份草稿住在这一半：每一个字段都是这个文件里的一个元素。
const adding = ref(false)
const saving = ref(false)
const formError = ref('')
const mailForm = reactive({
  preset: PRESETS[0].title,
  address: '',
  username: '',
  password: '',
  imap_host: PRESETS[0].imap,
  imap_port: 993,
  smtp_host: PRESETS[0].smtp,
  smtp_port: PRESETS[0].smtpPort,
  security: PRESETS[0].security,
  grants: [] as string[],
})
function applyPreset(title: string) {
  const preset = PRESETS.find((p) => p.title === title)
  if (!preset) return
  Object.assign(mailForm, {
    preset: title,
    imap_host: preset.imap,
    smtp_host: preset.smtp,
    smtp_port: preset.smtpPort,
    security: preset.security,
  })
}

/** 表单上只填地址，账号名跟着它走（填了账号名的老表单才会不一样）；`preset` 是这一页
 *  自己的东西，服务端不认识它。拼好之后连结果一起交给页面，请求在那一半。 */
function save() {
  if (saving.value) return
  saving.value = true
  formError.value = ''
  emit(
    'save',
    { ...mailForm, username: mailForm.username || mailForm.address, preset: undefined },
    (ok: boolean, message?: string) => {
      saving.value = false
      if (ok) {
        adding.value = false
        return
      }
      formError.value = message ?? ''
    }
  )
}
</script>

<template>
  <div class="settings-page">
    <!-- Deliberately not an AppPage: this view is one page of the user settings
         overlay (layouts/user/Settings.vue, meta.settingsOverlay). The overlay
         already owns the content column (components/common/SettingsOverlay,
         `.so__content` — 720 centred, 24 in), so a page frame here would nest a
         second header and a second column; the shared settings-card shell
         (styles/settings-card.css) is this kind of page's frame
         (docs/design-system.md §3.5). -->
    <header class="conn__head">
      <div>
        <h1 class="t-page-title">{{ t('account.settings.connections') }}</h1>
        <p class="settings-page__lede">{{ t('account.connections.lede') }}</p>
      </div>
      <BaseButton
        icon="mdi-refresh"
        size="sm"
        :loading="loading"
        :aria-label="t('account.connections.refresh')"
        :title="t('account.connections.refresh')"
        @click="emit('refresh')"
      />
    </header>

    <p v-if="error" role="alert" class="conn__notice c-danger">{{ error }}</p>

    <section class="settings-card" :aria-label="t('account.connections.pendingTitle')">
      <div class="settings-card__title">{{ t('account.connections.pendingTitle') }}</div>
      <BaseEmptyState
        v-if="!pending.length"
        size="inline"
        class="settings-empty"
        :title="t('account.connections.pendingEmpty')"
      />
      <div v-for="d in pending" :key="d.id" class="conn-row" :data-draft="d.id">
        <dl class="conn-spec">
          <dt>{{ t('account.connections.to') }}</dt>
          <dd>{{ d.to.join('、') }}</dd>
          <template v-if="d.cc.length">
            <dt>{{ t('account.connections.cc') }}</dt>
            <dd>{{ d.cc.join('、') }}</dd>
          </template>
          <dt>{{ t('account.connections.subject') }}</dt>
          <dd class="conn-spec__subject">{{ d.subject }}</dd>
          <dt>{{ t('account.connections.body') }}</dt>
          <dd class="conn-body">{{ d.body }}</dd>
          <dt>{{ t('account.connections.attachments') }}</dt>
          <dd>{{ attachmentList(d) }}</dd>
          <dt>{{ t('account.connections.from') }}</dt>
          <dd>{{ projectName(d.project_id) }} · <UserRef :handle="d.created_by" :project-id="d.project_id" /></dd>
        </dl>
        <div class="conn-actions">
          <BaseButton kind="ghost" size="sm" :loading="busy === `${d.id}:discard`" @click="emit('discard', d)">
            {{ t('account.connections.discard') }}
          </BaseButton>
          <BaseButton kind="primary" size="sm" :loading="busy === `${d.id}:send`" @click="confirming = d">
            {{ t('account.connections.send') }}
          </BaseButton>
        </div>
      </div>
    </section>

    <section class="settings-card" :aria-label="t('account.connections.accountsTitle')">
      <div class="settings-card__head">
        <div class="settings-card__title">{{ t('account.connections.accountsTitle') }}</div>
        <div class="conn__add">
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-email-plus-outline" @click="adding = true">
            {{ t('account.connections.addMail') }}
          </BaseButton>
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-link-variant-plus"
            :disabled="feishuMissing || feishuAsking"
            :loading="busy === 'feishu:connect'"
            @click="emit('connect-feishu')"
          >
            {{ t('integrations.member.connect') }}
          </BaseButton>
        </div>
      </div>
      <p v-if="feishuMissing" class="settings-card__desc">{{ t('integrations.member.notConfigured') }}</p>
      <BaseEmptyState
        v-if="!integrations.length && !loading"
        size="inline"
        class="settings-empty"
        :title="t('account.connections.accountsEmpty')"
      />
      <div v-for="row in integrations" :key="row.id" class="conn-row" :data-integration="row.id">
        <div class="conn-row__head">
          <div class="conn-row__id">
            <div class="conn-row__name">
              {{ row.provider === 'mail' ? t('account.connections.mail') : t('account.connections.feishu') }} ·
              {{ row.label }}
            </div>
            <div v-if="row.last_error" class="conn-row__sub c-danger">{{ row.last_error }}</div>
            <!-- 平台应用那一行在授权回来之前什么也做不了（凭据是管理员的，账号是你的），
                 所以这里说的是「连结上了没有」，不是「工作正常」—— 旁边那个状态在授权
                 之前说的「正常」只到「这一行本身没问题」为止。 -->
            <div v-else-if="row.shared_app && !row.user_authorized" class="conn-row__sub">
              {{ t('integrations.member.notConnected') }}
            </div>
          </div>
          <span class="conn-state" :class="row.status === 'ok' ? 'conn-state--ok' : 'conn-state--bad'">
            <span class="conn-state__dot" aria-hidden="true" />{{ statusLabel(row.status) }}
          </span>
        </div>
        <v-select
          :model-value="row.grants"
          autocomplete="off"
          :items="projects"
          item-title="name"
          item-value="id"
          multiple
          chips
          density="compact"
          :label="t('account.connections.grants')"
          :loading="busy === `${row.id}:grants`"
          @update:model-value="(v: string[]) => emit('set-grants', row, v)"
        />
        <div class="conn-actions">
          <BaseButton kind="ghost" size="sm" :loading="busy === `${row.id}:check`" @click="emit('recheck', row)">
            {{ t('account.connections.check') }}
          </BaseButton>
          <!-- 自带凭据的老连接：授权个人账号是它在搜索上差的那一步，按钮留着是为了让
               这些行照旧能用（`feishu_settings` 优先用它自己那套凭据）。走平台应用的那
               些行没有这一颗 —— 它们连接的方式就是上面那颗「连接飞书」。 -->
          <BaseButton
            v-if="row.provider === 'feishu' && !row.shared_app"
            kind="ghost"
            size="sm"
            :loading="busy === `${row.id}:auth`"
            @click="emit('authorize', row)"
          >
            {{ row.user_authorized ? t('account.connections.reauthorize') : t('account.connections.authorize') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="removing = row">{{ t('account.connections.remove') }}</BaseButton>
        </div>
      </div>
    </section>

    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('account.connections.sendTitle', { subject: confirming?.subject ?? '' })"
      :confirm-label="t('account.connections.sendShort')"
      @update:model-value="confirming = null"
      @confirm="confirmSend"
    >
      <template v-if="confirming">
        {{
          confirming.attachments.length
            ? t('account.connections.sendBodyAttachments', {
                to: [...confirming.to, ...confirming.cc].join('、'),
                n: confirming.attachments.length,
              })
            : t('account.connections.sendBody', { to: [...confirming.to, ...confirming.cc].join('、') })
        }}
      </template>
    </ConfirmDialog>

    <ConfirmDialog
      :model-value="!!removing"
      :title="t('account.connections.removeTitle', { label: removing?.label ?? '' })"
      :confirm-label="t('account.connections.remove')"
      danger
      @update:model-value="removing = null"
      @confirm="confirmRemove"
    >
      {{ t('account.connections.removeBody') }}
    </ConfirmDialog>

    <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。
         只剩邮箱 —— 飞书那一栏不是一张表单，是上面那颗「连接飞书」。 -->
    <AdaptiveDialog
      :model-value="adding"
      :title="t('account.connections.addMail')"
      :primary-label="t('account.connections.testAndSave')"
      :primary-loading="saving"
      :max-width="560"
      @update:model-value="adding = false"
      @primary="save"
    >
      <template v-if="adding">
        <v-select
          :model-value="mailForm.preset"
          autocomplete="off"
          :items="PRESETS.map((p) => p.title)"
          :label="t('account.connections.form.service')"
          @update:model-value="applyPreset"
        />
        <v-text-field v-model="mailForm.address" autocomplete="email" :label="t('account.connections.form.address')" />
        <v-text-field
          v-model="mailForm.password"
          autocomplete="new-password"
          type="password"
          :label="t('account.connections.form.password')"
          :hint="t('account.connections.form.passwordHint')"
          persistent-hint
        />
        <div class="conn-form-row mt-2">
          <v-text-field v-model="mailForm.imap_host" autocomplete="off" :label="t('account.connections.form.imap')" />
          <v-text-field
            v-model.number="mailForm.imap_port"
            autocomplete="off"
            type="number"
            :label="t('account.connections.form.port')"
          />
        </div>
        <div class="conn-form-row">
          <v-text-field v-model="mailForm.smtp_host" autocomplete="off" :label="t('account.connections.form.smtp')" />
          <v-text-field
            v-model.number="mailForm.smtp_port"
            autocomplete="off"
            type="number"
            :label="t('account.connections.form.port')"
          />
        </div>
        <v-select
          v-model="mailForm.security"
          autocomplete="off"
          :items="['ssl', 'starttls']"
          :label="t('account.connections.form.security')"
        />
        <v-select
          v-model="mailForm.grants"
          autocomplete="off"
          :items="projects"
          item-title="name"
          item-value="id"
          multiple
          chips
          :label="t('account.connections.grants')"
        />
        <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
      </template>
    </AdaptiveDialog>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.conn__head {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  justify-content: space-between;
}

.conn__notice {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
}

.conn__add {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.conn-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 24px 16px;
  border-top: 1px solid var(--line);
}

.conn-row__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.conn-row__id {
  flex: 1;
  min-width: 0;
}

.conn-row__name {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.conn-row__sub {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.conn-state {
  display: inline-flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
}

.conn-state__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
}

.conn-state--ok {
  color: var(--ok-ink);
}

.conn-state--ok .conn-state__dot {
  background: var(--ok);
}

.conn-state--bad {
  color: var(--danger-ink);
}

.conn-state--bad .conn-state__dot {
  background: var(--danger);
}

.conn-spec {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 12px;
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.conn-spec dt {
  color: var(--faint);
}

.conn-spec dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.conn-spec__subject {
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
}

.conn-body {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
}

.conn-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}

.conn-form-row {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: 8px;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .conn-row {
    padding: 12px 16px 14px;
  }
}
</style>
