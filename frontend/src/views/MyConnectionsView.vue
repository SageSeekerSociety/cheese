<script setup lang="ts">
// 我的连接：你自己的邮箱和飞书。AI 队友只在你勾选的项目里用它们，用的是你的账号；
// 它们写的邮件只进草稿箱，发不发由你在这里看过之后决定。
//
// 画的那一半在 `MyConnectionsViewView.vue`：这里只留下请求、地址和 toast，画面只收
// props、只 emit。邮箱表单的那份草稿跟着画面走 —— 每个字段都是那边的元素。
import type { Integration, MailDraft } from '../api'
import type { FeishuOwnApp } from '../api/feishu'
import type { Project } from '../cx_types'

import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  checkIntegration,
  connectMail,
  deleteIntegration,
  discardMailDraft,
  listMyIntegrations,
  listMyMailDrafts,
  sendMailDraft,
  updateIntegration,
} from '../api'
import { claimFeishuHandoff, connectFeishu, feishuAuthorizeUrl, feishuSetup } from '../api/feishu'

import MyConnectionsViewView from './MyConnectionsViewView.vue'

import { copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { renderNoticeMessage } from '@/lib/noticeText'
import { toast } from '@/lib/toast'
import { queryClient } from '@/query/client'
import { projectsQuery } from '@/query/projects'

const route = useRoute()
const router = useRouter()
const integrations = ref<Integration[]>([])
const drafts = ref<MailDraft[]>([])
const projects = ref<Project[]>([])
/** 成员自己公司的应用要填的授权回调地址（「连接飞书」那张表单上的重定向 URL）。读不到
 *  就是空的：表单照样能填，只是那一格不画。 */
const feishuRedirectUri = ref('')
const loading = ref(false)
const error = ref('')
/** 从飞书授权回来时地址栏里的结果（`?feishu=`）：一个结果码，或者平台那句拒绝的
 *  apiError key；`feishu_detail` 是飞书自己的原话。按读者的语言说出来，不认识的
 *  码只说授权失败。 */
function feishuOutcome(query: Record<string, unknown>): string {
  const code = typeof query.feishu === 'string' ? query.feishu : ''
  if (!code) return ''
  if (code === 'ok') return t('account.connections.feishuAuthorized')
  const known = FEISHU_OUTCOMES[code]
  const sentence = known
    ? t(`account.connections.feishuOutcome.${known}`)
    : renderNoticeMessage({ key: code }, t('account.connections.feishuOutcome.failed'))
  const detail = typeof query.feishu_detail === 'string' ? query.feishu_detail : ''
  return detail ? t('account.connections.feishuOutcome.withDetail', { sentence, detail }) : sentence
}
const FEISHU_OUTCOMES: Record<string, string> = {
  invalid_link: 'invalidLink',
  denied: 'denied',
  deleted: 'deleted',
  exchange_failed: 'failed',
}
const busy = ref('')

async function load() {
  loading.value = true
  error.value = ''
  // 回调地址与下面三项并行发起，但单独结算：问不到不该把整页的连接一起掀掉。
  const setupRead = feishuSetup().then(
    (got) => got.redirect_uri,
    () => ''
  )
  try {
    const [mine, waiting, projectList] = await Promise.all([
      listMyIntegrations(),
      listMyMailDrafts('drafted'),
      queryClient.fetchQuery({ ...projectsQuery(), staleTime: 0 }),
    ])
    integrations.value = mine.data
    drafts.value = waiting.data
    projects.value = projectList
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('account.connections.loadFailed')
  } finally {
    loading.value = false
  }
  feishuRedirectUri.value = await setupRead
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
 * 先要自己那一行（`POST /me/integrations/feishu`，带上成员自己公司应用的 App ID / Secret），
 * 再拿它的授权地址跳去飞书 —— 回调要靠行 id 找回它是谁（见 `feishu_callback` 的 `state`），
 * 所以顺序不能反过来。表单的结果用 `done` 带回去：没成时把服务端那句话留在表单底下。
 */
async function connectFeishuAccount(app: FeishuOwnApp, done: (ok: boolean, message?: string) => void) {
  let row: Integration
  try {
    row = await connectFeishu(app)
  } catch (e) {
    done(false, e instanceof Error ? e.message : t('account.connections.connectFailed'))
    return
  }
  done(true)
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

/** 保存那张邮箱表单。表单的草稿和报错都在画面那一半（字段都是那儿的元素），所以结果
 *  用回调带回去：成了关掉对话框，没成把服务端那句话留在表单底下。 */
async function save(body: Record<string, unknown>, done: (ok: boolean, message?: string) => void) {
  try {
    const row = await connectMail(body)
    integrations.value = [...integrations.value, row]
    done(true)
  } catch (e) {
    done(false, e instanceof Error ? e.message : t('account.connections.connectFailed'))
  }
}

/** 从飞书回来：回调不存东西，只把结果替发起人留着（`?feishu_handoff=`）。这一页登录着
 *  去领，只有发起授权的人领得到 —— 别人点了同一条授权链接，令牌也进不了这一行。领到的
 *  就是这次授权的结果，和别的回执（`?feishu=`）一样说一次；地址栏里的领取号随即拿掉。 */
async function landedFeishu(): Promise<Record<string, unknown>> {
  const { feishu_handoff: handoff, feishu_integration: integration, ...rest } = route.query
  if (typeof handoff !== 'string' || !handoff || typeof integration !== 'string') return route.query
  void router.replace({ query: rest, hash: route.hash })
  try {
    return { ...rest, ...(await claimFeishuHandoff(integration, handoff)).authorization }
  } catch {
    return { ...rest, feishu: 'exchange_failed' }
  }
}

onMounted(async () => {
  // 从飞书授权回来时地址栏里的结果（`?feishu=`）是这一次跳转的回执，弹一次就够。
  const landed = await landedFeishu()
  const outcome = feishuOutcome(landed)
  await load()
  if (outcome) {
    if (landed.feishu === 'ok') toast.success(outcome)
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
    :feishu-redirect-uri="feishuRedirectUri"
    @refresh="load"
    @discard="discard"
    @send="send"
    @connect-feishu="connectFeishuAccount"
    @set-grants="setGrants"
    @recheck="recheck"
    @authorize="authorize"
    @remove="remove"
    @copy="(text: string) => copyText(text, t('integrations.member.ownApp.copied'))"
    @save="save"
  />
</template>
