<script setup lang="ts">
// 我的连接：你自己的邮箱和飞书。AI 队友只在你勾选的项目里用它们，用的是你的账号；
// 它们写的邮件只进草稿箱，发不发由你在这里看过之后决定。
//
// 取数、飞书授权那两跳、发信/删除/改授权范围的请求、全局 toast 都在这一半；画面在
// MyConnectionsViewView.vue，只收 props 只发事件。草稿「来自」那个人名和去处也在这
// 里算好（名册查询、路由判断在这边），展示件 UserRef 只认 props。
import type { Integration, MailDraft } from '../api'
import type { FeishuAvailability } from '../api/feishu'
import type { Project } from '../cx_types'

import { computed, getCurrentInstance, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

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
import { memberName } from '../lib/agentNames'
import { goAuthorize } from '../lib/desktopApp'
import { renderNoticeMessage } from '../lib/noticeText'
import { userRefRoute, type UserRefTarget } from '../lib/userRef'

import MyConnectionsViewView from './MyConnectionsViewView.vue'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

/** 接入表单里那几格。视图那边声明了同样一份，`save(form)` 时结构对上就行。 */
interface MailForm {
  preset: string
  address: string
  username: string
  password: string
  imap_host: string
  imap_port: number
  smtp_host: string
  smtp_port: number
  security: string
  grants: string[]
}

const route = useRoute()
const router = useRouter()
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
const busy = ref('')

// ── 草稿「来自」那一栏的人 ───────────────────────────────────────────────────
// 名册只在装了 pinia 的树里才有（孤立渲染的卡片 / 单测没有），和 composables/useUserRef
// 里同一套兜底：没有就只画 @handle，不给去处。展示件 UserRef 那边只认 props。
const app = getCurrentInstance()?.appContext.config.globalProperties
const workspace = app?.$pinia ? useWorkspaceStore() : null
const people = computed<Record<string, { name: string; to: UserRefTarget | null }>>(() => {
  const out: Record<string, { name: string; to: UserRefTarget | null }> = {}
  for (const d of drafts.value) {
    const handle = d.created_by
    if (!handle || out[handle]) continue
    const row = workspace?.members.find((m) => m.user_handle === handle)
    out[handle] = {
      name: memberName(row) || handle,
      to: app?.$router ? userRefRoute(handle, d.project_id) : null,
    }
  }
  return out
})
function navigate(target: UserRefTarget | null) {
  if (target) void router.push(target)
}

// ── 接入表单 ───────────────────────────────────────────────────────────────
const adding = ref(false)
const saving = ref(false)
const formError = ref('')

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

// 发信、连接、改授权范围这些都是一次性动作：结果跟这一次点击走，用全局 toast
// 说一声（§3.11）。读整页失败仍留在页顶上那条里（error）。
async function act<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
  busy.value = key
  try {
    return await fn()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('account.connections.actionFailed'))
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
  const out = await act(`${row.id}:delete`, () => deleteIntegration(row.id))
  if (out) integrations.value = integrations.value.filter((x) => x.id !== row.id)
}

async function send(draft: MailDraft) {
  const out = await act(`${draft.id}:send`, () => sendMailDraft(draft.id))
  if (out) {
    drafts.value = drafts.value.filter((d) => d.id !== draft.id)
    const refused = out.refused.length ? t('account.connections.refused', { list: out.refused.join('、') }) : ''
    const notes = out.notes.length ? t('account.connections.notes', { list: out.notes.join('，') }) : ''
    toast.success(t('account.connections.sent', { subject: draft.subject }) + refused + notes)
  } else {
    await load()
  }
}

async function discard(draft: MailDraft) {
  const out = await act(`${draft.id}:discard`, () => discardMailDraft(draft.id))
  if (out) drafts.value = drafts.value.filter((d) => d.id !== draft.id)
}

async function save(form: MailForm) {
  saving.value = true
  formError.value = ''
  try {
    const row = await connectMail({
      ...form,
      username: form.username || form.address,
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

onMounted(async () => {
  // 从飞书授权回来时地址栏里的结果（`?feishu=`）是这一次跳转的回执，弹一次就够。
  const outcome = feishuOutcome()
  await load()
  if (outcome) {
    if (route.query.feishu === 'ok') toast.success(outcome)
    else toast.error(outcome)
  }
})
</script>

<template>
  <MyConnectionsViewView
    :integrations="integrations"
    :drafts="drafts"
    :projects="projects"
    :loading="loading"
    :error="error"
    :busy="busy"
    :feishu-missing="feishuMissing"
    :add-open="adding"
    :saving="saving"
    :form-error="formError"
    :people="people"
    @refresh="load"
    @update:add-open="adding = $event"
    @save="save"
    @set-grants="setGrants"
    @recheck="recheck"
    @authorize="authorize"
    @connect-feishu="connectFeishuAccount"
    @discard="discard"
    @send="send"
    @remove="remove"
    @navigate="navigate"
  />
</template>
