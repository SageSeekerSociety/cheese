<script setup lang="ts">
// 我的连接：你自己的邮箱和飞书。AI 队友只在你勾选的项目里用它们，用的是你的账号；
// 它们写的邮件只进草稿箱，发不发由你在这里看过之后决定。
import type { Integration, MailDraft } from '../api'
import type { Project } from '../cx_types'

import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  checkIntegration,
  connectFeishu,
  connectMail,
  deleteIntegration,
  discardMailDraft,
  feishuAuthorizeUrl,
  listMyIntegrations,
  listMyMailDrafts,
  listProjects,
  sendMailDraft,
  updateIntegration,
} from '../api'

const route = useRoute()
const integrations = ref<Integration[]>([])
const drafts = ref<MailDraft[]>([])
const projects = ref<Project[]>([])
const loading = ref(false)
const error = ref('')
const notice = ref(typeof route.query.feishu === 'string' ? route.query.feishu : '')
const busy = ref('')
const confirming = ref<MailDraft | null>(null)
const removing = ref<Integration | null>(null)

const STATUS: Record<Integration['status'], string> = {
  ok: '正常',
  auth_failed: '授权失效',
  unreachable: '连不上',
  error: '出错',
}

const PRESETS = [
  { title: 'QQ 邮箱（用授权码）', imap: 'imap.qq.com', smtp: 'smtp.qq.com', smtpPort: 465, security: 'ssl' },
  { title: '163 邮箱（用授权码）', imap: 'imap.163.com', smtp: 'smtp.163.com', smtpPort: 465, security: 'ssl' },
  {
    title: '腾讯企业邮',
    imap: 'imap.exmail.qq.com',
    smtp: 'smtp.exmail.qq.com',
    smtpPort: 465,
    security: 'ssl',
  },
  {
    title: '阿里企业邮',
    imap: 'imap.qiye.aliyun.com',
    smtp: 'smtp.qiye.aliyun.com',
    smtpPort: 465,
    security: 'ssl',
  },
  { title: 'Gmail（用应用专用密码）', imap: 'imap.gmail.com', smtp: 'smtp.gmail.com', smtpPort: 465, security: 'ssl' },
  {
    title: 'Outlook / Microsoft 365',
    imap: 'outlook.office365.com',
    smtp: 'smtp.office365.com',
    smtpPort: 587,
    security: 'starttls',
  },
  { title: '其他（自己填）', imap: '', smtp: '', smtpPort: 465, security: 'ssl' },
]

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
    error.value = e instanceof Error ? e.message : '未能读取连接'
  } finally {
    loading.value = false
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
    error.value = e instanceof Error ? e.message : '操作没有成功'
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
  if (out) window.location.href = out.url
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
    const refused = out.refused.length ? `；这些收件人被服务器拒收：${out.refused.join('、')}` : ''
    notice.value = `已发送「${draft.subject}」${refused}${out.notes.length ? `（${out.notes.join('，')}）` : ''}`
  } else {
    await load()
  }
}

async function discard(draft: MailDraft) {
  const out = await act(`${draft.id}:discard`, () => discardMailDraft(draft.id))
  if (out) drafts.value = drafts.value.filter((d) => d.id !== draft.id)
}

// ── 接入 ───────────────────────────────────────────────────────────────────
const adding = ref<'mail' | 'feishu' | null>(null)
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
const feishuForm = reactive({
  label: '飞书',
  app_id: '',
  app_secret: '',
  domain: 'feishu',
  folders: '',
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
    const row =
      adding.value === 'mail'
        ? await connectMail({ ...mailForm, username: mailForm.username || mailForm.address, preset: undefined })
        : await connectFeishu({
            ...feishuForm,
            folders: feishuForm.folders
              .split(/[\s,，]+/)
              .map((f) => f.trim())
              .filter(Boolean),
          })
    integrations.value = [...integrations.value, row]
    adding.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '没有连上'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="connections">
    <header class="connections__head">
      <h1 class="t-page-title">我的连接</h1>
      <v-btn variant="text" :loading="loading" @click="load">刷新</v-btn>
    </header>
    <p class="t-body c-muted mb-6">
      接入你自己的邮箱或飞书，并勾选允许哪些项目的 AI
      队友使用。它们用的是你的账号：可以搜索和阅读邮件、把回复写进你的草稿箱、读写你有权限的飞书文档；
      <strong>邮件一律要你在下面核对之后才会发出</strong>
    </p>
    <p v-if="notice" role="status" class="t-body mb-4">{{ notice === 'ok' ? '飞书授权成功' : notice }}</p>
    <p v-if="error" role="alert" class="t-body c-danger mb-4">{{ error }}</p>

    <section class="mb-8" aria-label="待发送的邮件">
      <h2 class="t-section mb-2">待你确认发送（{{ pending.length }}）</h2>
      <p v-if="!pending.length" class="t-meta c-faint">没有等待发送的草稿</p>
      <ul class="conn-list">
        <li v-for="d in pending" :key="d.id" class="conn-row" :data-draft="d.id">
          <dl class="conn-spec t-meta">
            <dt>收件人</dt>
            <dd>{{ d.to.join('、') }}</dd>
            <template v-if="d.cc.length">
              <dt>抄送</dt>
              <dd>{{ d.cc.join('、') }}</dd>
            </template>
            <dt>主题</dt>
            <dd class="t-body">{{ d.subject }}</dd>
            <dt>正文</dt>
            <dd class="conn-body">{{ d.body }}</dd>
            <dt>附件</dt>
            <dd>
              {{ d.attachments.length ? d.attachments.map((a) => `${a.name}（${a.size} 字节）`).join('、') : '无' }}
            </dd>
            <dt>来自</dt>
            <dd>{{ projectName(d.project_id) }} · {{ d.created_by }} 起草</dd>
          </dl>
          <div class="conn-actions">
            <v-btn
              color="primary"
              variant="flat"
              size="small"
              :loading="busy === `${d.id}:send`"
              @click="confirming = d"
            >
              确认发送
            </v-btn>
            <v-btn variant="text" size="small" :loading="busy === `${d.id}:discard`" @click="discard(d)">
              不发了
            </v-btn>
          </div>
        </li>
      </ul>
    </section>

    <section aria-label="连接">
      <div class="connections__head">
        <h2 class="t-section">连接</h2>
        <div>
          <v-btn variant="text" size="small" @click="adding = 'mail'">接入邮箱</v-btn>
          <v-btn variant="text" size="small" @click="adding = 'feishu'">接入飞书</v-btn>
        </div>
      </div>
      <p v-if="!integrations.length && !loading" class="t-meta c-faint">还没有接入任何邮箱或飞书</p>
      <ul class="conn-list">
        <li v-for="row in integrations" :key="row.id" class="conn-row" :data-integration="row.id">
          <div class="conn-row__head">
            <div class="conn-row__id">
              <div class="t-body">{{ row.provider === 'mail' ? '邮箱' : '飞书' }} · {{ row.label }}</div>
              <div v-if="row.last_error" class="t-meta c-danger">{{ row.last_error }}</div>
            </div>
            <v-chip size="small" variant="tonal" :color="row.status === 'ok' ? 'success' : 'error'">
              {{ STATUS[row.status] }}
            </v-chip>
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
            label="允许这些项目的 AI 队友使用"
            :loading="busy === `${row.id}:grants`"
            @update:model-value="(v: string[]) => setGrants(row, v)"
          />
          <div class="conn-actions">
            <v-btn variant="text" size="small" :loading="busy === `${row.id}:check`" @click="recheck(row)">
              测试连接
            </v-btn>
            <v-btn
              v-if="row.provider === 'feishu'"
              variant="text"
              size="small"
              :loading="busy === `${row.id}:auth`"
              @click="authorize(row)"
            >
              {{ row.user_authorized ? '重新授权个人账号' : '授权个人账号（用于搜索）' }}
            </v-btn>
            <v-btn variant="text" size="small" color="on-surface-variant" @click="removing = row">删除</v-btn>
          </div>
        </li>
      </ul>
    </section>

    <v-dialog :model-value="!!confirming" max-width="480" @update:model-value="confirming = null">
      <v-card v-if="confirming">
        <v-card-title class="t-dialog-title">发送「{{ confirming.subject }}」</v-card-title>
        <v-card-text class="t-body">
          将从你的邮箱发给 {{ [...confirming.to, ...confirming.cc].join('、') }}
          <template v-if="confirming.attachments.length">，带 {{ confirming.attachments.length }} 个附件</template>
          。发出后无法撤回
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirming = null">取消</v-btn>
          <v-btn variant="text" color="primary" @click="send(confirming)">发送</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!removing" max-width="420" @update:model-value="removing = null">
      <v-card v-if="removing">
        <v-card-title class="t-dialog-title">删除「{{ removing.label }}」</v-card-title>
        <v-card-text class="t-body">删除后 AI 队友不再能用它，保存的密码或密钥一并删除</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="removing = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="remove(removing)">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!adding" max-width="560" @update:model-value="adding = null">
      <v-card v-if="adding">
        <v-card-title class="t-dialog-title">{{ adding === 'mail' ? '接入邮箱' : '接入飞书' }}</v-card-title>
        <v-card-text>
          <template v-if="adding === 'mail'">
            <v-select
              :model-value="mailForm.preset"
              autocomplete="off"
              :items="PRESETS.map((p) => p.title)"
              label="邮箱服务"
              @update:model-value="applyPreset"
            />
            <v-text-field v-model="mailForm.address" autocomplete="email" label="邮箱地址" />
            <v-text-field
              v-model="mailForm.password"
              autocomplete="new-password"
              type="password"
              label="密码或授权码"
              hint="QQ、163 等要在邮箱设置里开启 IMAP/SMTP 并生成授权码；Gmail 用应用专用密码"
              persistent-hint
            />
            <div class="conn-form-row mt-2">
              <v-text-field v-model="mailForm.imap_host" autocomplete="off" label="IMAP 服务器" />
              <v-text-field v-model.number="mailForm.imap_port" autocomplete="off" type="number" label="端口" />
            </div>
            <div class="conn-form-row">
              <v-text-field v-model="mailForm.smtp_host" autocomplete="off" label="SMTP 服务器" />
              <v-text-field v-model.number="mailForm.smtp_port" autocomplete="off" type="number" label="端口" />
            </div>
            <v-select v-model="mailForm.security" autocomplete="off" :items="['ssl', 'starttls']" label="加密方式" />
            <v-select
              v-model="mailForm.grants"
              autocomplete="off"
              :items="projects"
              item-title="name"
              item-value="id"
              multiple
              chips
              label="允许这些项目的 AI 队友使用"
            />
          </template>
          <template v-else>
            <v-text-field v-model="feishuForm.label" autocomplete="off" label="名称" />
            <v-text-field v-model="feishuForm.app_id" autocomplete="off" label="App ID（企业自建应用）" />
            <v-text-field
              v-model="feishuForm.app_secret"
              autocomplete="new-password"
              type="password"
              label="App Secret"
            />
            <v-select
              v-model="feishuForm.domain"
              autocomplete="off"
              :items="[
                { value: 'feishu', title: '飞书（feishu.cn）' },
                { value: 'lark', title: 'Lark（larksuite.com）' },
              ]"
              label="版本"
            />
            <v-text-field
              v-model="feishuForm.folders"
              autocomplete="off"
              label="应用能看到的文件夹 token（可选，多个用逗号隔开）"
              hint="只用应用凭据时飞书不提供全文搜索，列在这里的文件夹会按文件名查找；授权个人账号后可以全文搜索"
              persistent-hint
            />
            <v-select
              v-model="feishuForm.grants"
              autocomplete="off"
              :items="projects"
              item-title="name"
              item-value="id"
              multiple
              chips
              label="允许这些项目的 AI 队友使用"
              class="mt-2"
            />
          </template>
          <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="adding = null">取消</v-btn>
          <v-btn variant="text" color="primary" :loading="saving" @click="save">测试并保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.connections {
  max-width: var(--page-w, 760px);
  margin: 0 auto;
  padding: 24px 16px 48px;
}
.connections__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.conn-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conn-row {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.conn-row__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}
.conn-row__id {
  flex: 1;
  min-width: 0;
}
.conn-spec {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
  margin: 0 0 8px;
}
.conn-spec dt {
  color: var(--faint);
}
.conn-spec dd {
  margin: 0;
}
.conn-body {
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
}
.conn-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.conn-form-row {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: 8px;
}
</style>
