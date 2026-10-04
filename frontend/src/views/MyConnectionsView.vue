<script setup lang="ts">
// 我的连接：你自己的邮箱和飞书。AI 队友只在你勾选的项目里用它们，用的是你的账号；
// 它们写的邮件只进草稿箱，发不发由你在这里看过之后决定。
import type { Integration, MailDraft } from '../api'
import type { FeishuAvailability } from '../api/feishu'
import type { Project } from '../cx_types'

import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  checkIntegration,
  connectMail,
  deleteIntegration,
  discardMailDraft,
  listMyIntegrations,
  listMyMailDrafts,
  listProjects,
  sendMailDraft,
  updateIntegration,
} from '../api'
import { connectFeishu, feishuAuthorizeUrl, feishuAvailability } from '../api/feishu'
import ConfirmDialog from '../components/base/ConfirmDialog.vue'
import AdaptiveDialog from '../components/common/AdaptiveDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { renderNoticeMessage } from '@/lib/noticeText'

const route = useRoute()
const integrations = ref<Integration[]>([])
const drafts = ref<MailDraft[]>([])
const projects = ref<Project[]>([])
/** 平台管理员配过飞书应用没有。没配时「连接飞书」是灰的 —— 点了必然失败，画成能点
 *  只会把人送到一句报错上。 */
const feishu = ref<FeishuAvailability>({ configured: false, app_id: '', domain: '' })
/** 上面那个问题问到答案没有。读不到时按钮不画灰 —— 没读到不等于没配，而「管理员还没
 *  配置飞书应用」是一句会被当真的话：宁可让人按下去，由服务端回那句实话。 */
const feishuKnown = ref(false)
/** 确定没配：按钮灰着，底下写明为什么。 */
const feishuMissing = computed(() => feishuKnown.value && !feishu.value.configured)
const loading = ref(false)
const error = ref('')
/** 从飞书授权回来时地址栏里的结果（`?feishu=`）：一个结果码，或者平台那句拒绝的
 *  apiError key；`feishu_detail` 是飞书自己的原话。按读者的语言说出来，不认识的
 *  码只说授权失败。 */
function feishuOutcome(): string {
  const code = typeof route.query.feishu === 'string' ? route.query.feishu : ''
  if (!code) return ''
  if (code === 'ok') return t('account.connections.feishuAuthorized')
  const known = FEISHU_OUTCOMES[code]
  const sentence = known
    ? t(`account.connections.feishuOutcome.${known}`)
    : renderNoticeMessage({ key: code }, t('account.connections.feishuOutcome.failed'))
  const detail = typeof route.query.feishu_detail === 'string' ? route.query.feishu_detail : ''
  return detail ? t('account.connections.feishuOutcome.withDetail', { sentence, detail }) : sentence
}
const FEISHU_OUTCOMES: Record<string, string> = {
  invalid_link: 'invalidLink',
  denied: 'denied',
  deleted: 'deleted',
  exchange_failed: 'failed',
}
const notice = ref(feishuOutcome())
const busy = ref('')
const confirming = ref<MailDraft | null>(null)
const removing = ref<Integration | null>(null)

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

const projectName = (id: string) => projects.value.find((p) => p.id === id)?.name ?? id
const pending = computed(() => drafts.value.filter((d) => d.status === 'drafted'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [mine, waiting, projectList] = await Promise.all([
      listMyIntegrations(),
      listMyMailDrafts('drafted'),
      listProjects(),
    ])
    integrations.value = mine.data
    drafts.value = waiting.data
    projects.value = projectList.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('account.connections.loadFailed')
  } finally {
    loading.value = false
  }
  // 应用配没配是另一件事，单独问、单独失败：问不到不该把整页的连接一起掀掉。
  try {
    feishu.value = await feishuAvailability()
    feishuKnown.value = true
  } catch {
    feishuKnown.value = false
  }
}

function replace(row: Integration) {
  integrations.value = integrations.value.map((x) => (x.id === row.id ? row : x))
}

async function act<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
  busy.value = key
  error.value = ''
  try {
    return await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('account.connections.actionFailed')
    return undefined
  } finally {
    busy.value = ''
  }
}

async function setGrants(row: Integration, grants: string[]) {
  const out = await act(`${row.id}:grants`, () => updateIntegration(row.id, { grants }))
  if (out) replace(out)
}

async function recheck(row: Integration) {
  const out = await act(`${row.id}:check`, () => checkIntegration(row.id))
  if (out) replace(out)
}

async function authorize(row: Integration) {
  const out = await act(`${row.id}:auth`, () => feishuAuthorizeUrl(row.id))
  if (out) goAuthorize(out.url)
}

/**
 * 「连接飞书」：两跳一步。
 *
 * 先要自己那一行（`POST /me/integrations/feishu`），再拿它的授权地址跳去飞书 —— 回调
 * 要靠行 id 找回它是谁（见 `feishu_callback` 的 `state`），所以顺序不能反过来。这一行
 * 此刻没有任何凭据：应用是平台管理员的，成员出的是自己的账号。
 */
async function connectFeishuAccount() {
  const row = await act('feishu:connect', () => connectFeishu())
  if (!row) return
  if (integrations.value.some((x) => x.id === row.id)) replace(row)
  else integrations.value = [...integrations.value, row]
  await authorize(row)
}

async function remove(row: Integration) {
  removing.value = null
  const out = await act(`${row.id}:delete`, () => deleteIntegration(row.id))
  if (out) integrations.value = integrations.value.filter((x) => x.id !== row.id)
}

async function send(draft: MailDraft) {
  confirming.value = null
  const out = await act(`${draft.id}:send`, () => sendMailDraft(draft.id))
  if (out) {
    drafts.value = drafts.value.filter((d) => d.id !== draft.id)
    const refused = out.refused.length ? t('account.connections.refused', { list: out.refused.join('、') }) : ''
    const notes = out.notes.length ? t('account.connections.notes', { list: out.notes.join('，') }) : ''
    notice.value = t('account.connections.sent', { subject: draft.subject }) + refused + notes
  } else {
    await load()
  }
}

async function discard(draft: MailDraft) {
  const out = await act(`${draft.id}:discard`, () => discardMailDraft(draft.id))
  if (out) drafts.value = drafts.value.filter((d) => d.id !== draft.id)
}

// ── 接入 ───────────────────────────────────────────────────────────────────
// 只剩邮箱这一张表单。飞书不再有「填 App ID / Secret」这一种接入方式 —— 它是成员点
// 一下「连接飞书」、跳到飞书授权页那一步（`connectFeishuAccount`），凭据在管理员那边。
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

async function save() {
  saving.value = true
  formError.value = ''
  try {
    const row = await connectMail({
      ...mailForm,
      username: mailForm.username || mailForm.address,
      preset: undefined,
    })
    integrations.value = [...integrations.value, row]
    adding.value = false
  } catch (e) {
    formError.value = e instanceof Error ? e.message : t('account.connections.connectFailed')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-page">
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
        @click="load"
      />
    </header>

    <p v-if="notice" role="status" class="conn__notice">
      {{ notice }}
    </p>
    <p v-if="error" role="alert" class="conn__notice c-danger">{{ error }}</p>

    <section class="settings-card" :aria-label="t('account.connections.pendingTitle')">
      <div class="settings-card__title">{{ t('account.connections.pendingTitle') }}</div>
      <p v-if="!pending.length" class="settings-empty">{{ t('account.connections.pendingEmpty') }}</p>
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
          <BaseButton kind="ghost" size="sm" :loading="busy === `${d.id}:discard`" @click="discard(d)">
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
            :disabled="feishuMissing"
            :loading="busy === 'feishu:connect'"
            @click="connectFeishuAccount"
          >
            {{ t('integrations.member.connect') }}
          </BaseButton>
        </div>
      </div>
      <p v-if="feishuMissing" class="settings-card__desc">{{ t('integrations.member.notConfigured') }}</p>
      <p v-if="!integrations.length && !loading" class="settings-empty">{{ t('account.connections.accountsEmpty') }}</p>
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
          @update:model-value="(v: string[]) => setGrants(row, v)"
        />
        <div class="conn-actions">
          <BaseButton kind="ghost" size="sm" :loading="busy === `${row.id}:check`" @click="recheck(row)">
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
            @click="authorize(row)"
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
      @confirm="confirming && send(confirming)"
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
      @confirm="removing && remove(removing)"
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
