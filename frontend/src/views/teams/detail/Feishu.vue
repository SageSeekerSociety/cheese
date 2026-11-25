<script setup lang="ts">
// 团队设置「飞书应用」：读这个团队的应用和本人自己的连接，保存、删除应用，连接、重新连接、
// 断开本人的飞书账号，复制。画面在 `FeishuView.vue`。同一页也挂在个人设置里（个人团队的
// 那一份），团队由外框递下来（`teamDataInjectionKey`）。
import type { FeishuConnection, TeamFeishuApp } from '@/api/feishu'

import { computed, inject, ref, watch } from 'vue'

import FeishuView from './FeishuView.vue'

import { deleteIntegration } from '@/api'
import {
  connectFeishu,
  feishuAuthorizeUrl,
  getTeamFeishuApp,
  removeTeamFeishuApp,
  saveTeamFeishuApp,
} from '@/api/feishu'
import { copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { goAuthorize } from '@/lib/desktopApp'
import { toast } from '@/lib/toast'

const team = inject(teamDataInjectionKey, ref())
const teamId = computed(() => team.value?.id ?? 0)
const personal = computed(() => !!team.value?.personal)
const app = ref<TeamFeishuApp | null>(null)
const canManage = computed(() => !!app.value?.can_manage)
const connection = computed<FeishuConnection | null>(() => app.value?.connection ?? null)
const loading = ref(false)
const loadError = ref('')
const connecting = ref(false)

async function load() {
  const id = teamId.value
  app.value = null
  loadError.value = ''
  if (!id) return
  loading.value = true
  try {
    const got = await getTeamFeishuApp(id)
    if (teamId.value === id) app.value = got
  } catch (e) {
    if (teamId.value === id) loadError.value = e instanceof Error ? e.message : t('integrations.team.loadFailed')
  } finally {
    if (teamId.value === id) loading.value = false
  }
}
watch(teamId, load, { immediate: true })

/** 存或删了应用之后重读一遍：换了 App ID 或删掉应用，服务端已经把本人的连接打回「需要
 *  重新授权」，写回来的那一份不带连接，留着旧的就会把已经作废的连接说成正常。读不到就说读不到，
 *  画面上有重试。 */
async function reread() {
  const id = teamId.value
  try {
    const got = await getTeamFeishuApp(id)
    if (teamId.value === id) app.value = got
  } catch (e) {
    if (teamId.value === id) loadError.value = e instanceof Error ? e.message : t('integrations.team.loadFailed')
  }
}

async function save(
  body: { app_id: string; app_secret: string; domain: string },
  done: (ok: boolean, message?: string) => void
) {
  try {
    await saveTeamFeishuApp(teamId.value, body)
    await reread()
    done(true)
    toast.success(t('integrations.team.saved'))
  } catch (e) {
    done(false, e instanceof Error ? e.message : t('integrations.team.saveFailed'))
  }
}

async function remove() {
  try {
    await removeTeamFeishuApp(teamId.value)
    await reread()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('integrations.team.saveFailed'))
  }
}

/** 连接两跳：先有本人那一行（只记是哪个团队的应用），再拿它的授权地址跳去飞书。回调要靠
 *  行 id 认出是谁授权的，所以顺序不能反过来。授权完回到「我的连接」，那里说成没成。 */
async function authorizeRow(id: string) {
  goAuthorize((await feishuAuthorizeUrl(id)).url)
}

async function run(step: () => Promise<void>) {
  connecting.value = true
  try {
    await step()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('integrations.team.connectFailed'))
  } finally {
    connecting.value = false
  }
}

const connect = () => run(async () => authorizeRow((await connectFeishu(teamId.value)).id))
const reconnect = (c: FeishuConnection) => run(() => authorizeRow(c.id))
const disconnect = (c: FeishuConnection) =>
  run(async () => {
    await deleteIntegration(c.id)
    await load()
  })
</script>

<template>
  <FeishuView
    :personal="personal"
    :app="app"
    :loading="loading"
    :load-error="loadError"
    :can-manage="canManage"
    :connection="connection"
    :connecting="connecting"
    @save="save"
    @remove="remove"
    @copy="(text: string) => copyText(text, t('integrations.team.copied'))"
    @retry="load"
    @connect="connect"
    @reconnect="reconnect"
    @disconnect="disconnect"
  />
</template>
