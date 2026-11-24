<!--
  The rendering half of MyConnectionsView.vue, split out by hand: props in, events out.
  The page keeps the requests, the route and the toasts; the mail form's draft lives here,
  because every field of it is an element in this file.
-->

<script setup lang="ts">
import type { Integration, MailDraft } from '../api'
import type { FeishuOwnApp } from '../api/feishu'
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
  /** 授权回调地址，自己公司的应用要把它填进「重定向 URL」；`''` 表示没问到。 */
  feishuRedirectUri: string
  /** 问回调地址失败时那句原话；`''` 表示没失败。 */
  feishuSetupError: string
}>()

const emit = defineEmits<{
  refresh: []
  /** 再问一次回调地址。 */
  'retry-feishu-setup': []
  discard: [draft: MailDraft]
  send: [draft: MailDraft]
  /** 成员自己公司的应用；`done` 带回成没成、以及没成时那句话。 */
  'connect-feishu': [app: FeishuOwnApp, done: (ok: boolean, message?: string) => void]
  'set-grants': [row: Integration, grants: string[]]
  recheck: [row: Integration]
  authorize: [row: Integration]
  remove: [row: Integration]
  /** 表单上那几段要贴进飞书后台的内容；复制和提示在页面里。 */
  copy: [text: string]
  /** 表单已经按服务端认的样子拼好了；`done` 带回成没成、以及没成时那句话。 */
  save: [body: Record<string, unknown>, done: (ok: boolean, message?: string) => void]
}>()

const pending = computed(() => props.drafts.filter((d) => d.status === 'drafted'))

const statusLabel = (status: Integration['status']) => t(`account.connections.status.${status}`)

/** 一行连接叫什么：邮箱是它的地址；飞书是它用的那个应用的 App ID —— 同一个人可以连好几家
 *  公司的应用，光写「飞书」分不出哪一行是哪一家。平台应用时代留下、没有 App ID 的那种只剩
 *  「飞书」。 */
function rowName(row: Integration): string {
  if (row.provider === 'mail') return `${t('account.connections.mail')} · ${row.label}`
  const appId = typeof row.config.app_id === 'string' ? row.config.app_id : ''
  return appId ? `${row.label} · ${appId}` : row.label
}

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
// 两张表单：邮箱，和「连接飞书」。企业自建应用只在它那家企业的飞书里能用，所以飞书那张
// 表单填的是成员自己公司的应用；保存后页面（`connectFeishuAccount`）直接跳去飞书授权。
// 表单自己那份草稿住在这一半：每一个字段都是这个文件里的一个元素。
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

/** 粘进飞书开发者后台「权限管理 → 批量导入/导出权限」的那份 JSON：只要用户身份权限，
 *  和后端 `feishu.USER_SCOPES` 一一对应，改一处就改另一处。 */
const FEISHU_SCOPES_IMPORT = JSON.stringify(
  { scopes: { tenant: [], user: ['offline_access', 'search:docs:read', 'docx:document'] } },
  null,
  2
)
const ownApp = ref(false)
const ownAppSaving = ref(false)
const ownAppError = ref('')
const ownAppForm = reactive({ domain: 'feishu', app_id: '', app_secret: '' })
const consoleUrl = computed(() =>
  ownAppForm.domain === 'lark' ? 'https://open.larksuite.com/app' : 'https://open.feishu.cn/app'
)

function saveOwnApp() {
  if (ownAppSaving.value) return
  ownAppSaving.value = true
  ownAppError.value = ''
  emit('connect-feishu', { ...ownAppForm }, (ok: boolean, message?: string) => {
    ownAppSaving.value = false
    if (ok) {
      ownApp.value = false
      return
    }
    ownAppError.value = message ?? ''
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
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-link-variant-plus" @click="ownApp = true">
            {{ t('integrations.member.connect') }}
          </BaseButton>
        </div>
      </div>
      <BaseEmptyState
        v-if="!integrations.length && !loading"
        size="inline"
        class="settings-empty"
        :title="t('account.connections.accountsEmpty')"
      />
      <div v-for="row in integrations" :key="row.id" class="conn-row" :data-integration="row.id">
        <div class="conn-row__head">
          <div class="conn-row__id">
            <div class="conn-row__name">{{ rowName(row) }}</div>
            <div v-if="row.last_error" class="conn-row__sub c-danger">{{ row.last_error }}</div>
            <!-- 飞书那一行在授权回来之前搜不了（搜索只认个人授权），所以这里说的是「连结上
                 了没有」，不是「工作正常」—— 旁边那个状态在授权之前说的「正常」只到「应用
                 凭据没问题」为止。 -->
            <div v-else-if="row.provider === 'feishu' && !row.user_authorized" class="conn-row__sub">
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
          <!-- 授权个人账号：连接时跳过一次飞书；没授权完、授权过期或想换账号时从这里再走一次。 -->
          <BaseButton
            v-if="row.provider === 'feishu'"
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
      :title="t('account.connections.removeTitle', { label: removing ? rowName(removing) : '' })"
      :confirm-label="t('account.connections.remove')"
      danger
      @update:model-value="removing = null"
      @confirm="confirmRemove"
    >
      {{ t('account.connections.removeBody') }}
    </ConfirmDialog>

    <!-- 飞书「用自己公司的应用」：先照着几步在自己公司的飞书里建好应用，再填凭据。
         保存时服务端拿它换一次应用令牌，成了就直接跳去飞书授权页。 -->
    <AdaptiveDialog
      :model-value="ownApp"
      :title="t('integrations.member.ownApp.title')"
      :primary-label="t('integrations.member.ownApp.submit')"
      :primary-loading="ownAppSaving"
      :max-width="560"
      @update:model-value="ownApp = false"
      @primary="saveOwnApp"
    >
      <!-- 字段在前、怎么建应用在后：打开就能填，已经建好应用的人不用先滚过五步说明。 -->
      <template v-if="ownApp">
        <p class="t-body mb-4">{{ t('integrations.member.ownApp.why') }}</p>
        <v-select
          v-model="ownAppForm.domain"
          autocomplete="off"
          :items="[
            { title: t('integrations.member.ownApp.feishu'), value: 'feishu' },
            { title: 'Lark', value: 'lark' },
          ]"
          :label="t('integrations.member.ownApp.domain')"
        />
        <v-text-field v-model="ownAppForm.app_id" autocomplete="off" :label="t('integrations.member.ownApp.appId')" />
        <v-text-field
          v-model="ownAppForm.app_secret"
          autocomplete="new-password"
          type="password"
          :label="t('integrations.member.ownApp.appSecret')"
        />
        <p v-if="ownAppError" role="alert" class="t-body c-danger mb-3">{{ ownAppError }}</p>
        <details class="conn-setup t-body" open>
          <summary>{{ t('integrations.member.ownApp.setupTitle') }}</summary>
          <ol class="conn-steps">
            <li>{{ t('integrations.member.ownApp.step1', { console: consoleUrl }) }}</li>
            <li>
              {{ t('integrations.member.ownApp.step2') }}
              <!-- 一行只读输入框 + 复制，和团队加入链接（TeamJoinLinkCard）同一种画法：地址
                   不折行，长了在框里横向滚，不会被看成两段。 -->
              <div v-if="feishuSetupError" class="conn-link">
                <p role="alert" class="c-danger conn-link__error">
                  {{ t('integrations.member.ownApp.redirectFailed', { error: feishuSetupError }) }}
                </p>
                <BaseButton kind="secondary" size="sm" @click="emit('retry-feishu-setup')">
                  {{ t('integrations.member.ownApp.retry') }}
                </BaseButton>
              </div>
              <div v-else-if="feishuRedirectUri" class="conn-link">
                <v-text-field
                  autocomplete="off"
                  :model-value="feishuRedirectUri"
                  :aria-label="t('integrations.member.ownApp.redirectLabel')"
                  readonly
                  hide-details
                  density="compact"
                  variant="outlined"
                  class="conn-link__field"
                />
                <BaseButton kind="secondary" size="sm" @click="emit('copy', feishuRedirectUri)">
                  {{ t('integrations.member.ownApp.copy') }}
                </BaseButton>
              </div>
            </li>
            <li>
              {{ t('integrations.member.ownApp.step3') }}
              <div class="conn-copy">
                <pre class="conn-code">{{ FEISHU_SCOPES_IMPORT }}</pre>
                <BaseButton kind="secondary" size="sm" @click="emit('copy', FEISHU_SCOPES_IMPORT)">
                  {{ t('integrations.member.ownApp.copy') }}
                </BaseButton>
              </div>
            </li>
            <li>
              {{ t('integrations.member.ownApp.step4') }}
              <div class="conn-copy">
                <pre class="conn-code">{{ t('integrations.member.ownApp.reason') }}</pre>
                <BaseButton kind="secondary" size="sm" @click="emit('copy', t('integrations.member.ownApp.reason'))">
                  {{ t('integrations.member.ownApp.copy') }}
                </BaseButton>
              </div>
            </li>
            <li>{{ t('integrations.member.ownApp.step5') }}</li>
          </ol>
        </details>
      </template>
    </AdaptiveDialog>

    <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。 -->
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

.conn-setup summary {
  color: var(--ink);
  font-weight: 600;
  cursor: pointer;
}

.conn-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 8px 0 0;
  padding-left: 20px;
  color: var(--text);
}

.conn-link {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 4px;
}

.conn-link__field {
  flex: 1 1 auto;
  min-width: 0;
}

.conn-link__error {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
}

/* 地址和下面两段代码块同一套等宽小字，长出框的部分在框里横向滚。 */
.conn-link__field :deep(input) {
  font-family: var(--font-mono);
  font-size: 12px;
}

.conn-copy {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 4px;
}

.conn-code {
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

  /* The add buttons take a line of their own under the title, starting where it
     starts: side by side, the title squeezed to a character's width and broke
     mid-word, and the buttons stacked in a column. */
  .settings-card__head {
    flex-wrap: wrap;
  }

  .conn__add {
    flex-basis: 100%;
    margin-top: 0;
    padding: 0 0 12px 16px;
  }
}
</style>
