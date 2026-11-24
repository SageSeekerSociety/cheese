<script setup lang="ts">
// 团队设置「飞书应用」：读这个团队的应用、保存、删除、复制。画面在 `FeishuView.vue`。
// 同一页也挂在个人设置里（个人团队的那一份），团队由外框递下来（`teamDataInjectionKey`）。
import type { TeamFeishuApp } from '@/api/feishu'

import { computed, inject, ref, watch } from 'vue'

import FeishuView from './FeishuView.vue'

import { getTeamFeishuApp, removeTeamFeishuApp, saveTeamFeishuApp } from '@/api/feishu'
import { copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { teamAllows } from '@/lib/teamPermissions'
import { toast } from '@/lib/toast'

const team = inject(teamDataInjectionKey, ref())
const teamId = computed(() => team.value?.id ?? 0)
const canManage = computed(() => teamAllows(team.value, 'edit_settings'))
const personal = computed(() => !!team.value?.personal)
const app = ref<TeamFeishuApp | null>(null)
const loading = ref(false)
const loadError = ref('')

async function load() {
  const id = teamId.value
  app.value = null
  loadError.value = ''
  // 只有能改团队设置的人读得到（服务端也这么判）；别人看到的是「只有管理员能设」。
  if (!id || !canManage.value) return
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
watch([teamId, canManage], load, { immediate: true })

async function save(
  body: { app_id: string; app_secret: string; domain: string },
  done: (ok: boolean, message?: string) => void
) {
  try {
    app.value = await saveTeamFeishuApp(teamId.value, body)
    done(true)
    toast.success(t('integrations.team.saved'))
  } catch (e) {
    done(false, e instanceof Error ? e.message : t('integrations.team.saveFailed'))
  }
}

async function remove() {
  try {
    app.value = await removeTeamFeishuApp(teamId.value)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('integrations.team.saveFailed'))
  }
}
</script>

<template>
  <FeishuView
    :personal="personal"
    :app="app"
    :loading="loading"
    :load-error="loadError"
    :can-manage="canManage"
    @save="save"
    @remove="remove"
    @copy="(text: string) => copyText(text, t('integrations.team.copied'))"
    @retry="load"
  />
</template>
