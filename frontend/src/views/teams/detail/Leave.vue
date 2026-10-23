<script setup lang="ts">
// 团队设置最后那一栏（`/teams/<地址>/leave`）：所有者转让或解散团队，其他成员退出。侧栏
// 团队那一行的 ⋯ 里也有这几样，做的是同一件事。这一页发请求、改侧栏那份名单，画法在同目录
// 的 `LeaveView.vue`。
//
// 转让成了，读者变成管理员，这一栏随之换成「退出团队」；解散或退出成了，这个团队不再是
// 读者的，回待办。
import type { Team, TeamMember } from '@/types'

import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import LeaveView from './LeaveView.vue'

import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { patchQuery } from '@/query/client'
import { keys } from '@/query/keys'
import AccountService from '@/services/account'
import { useWorkspaceStore } from '@/stores/workspace'

const router = useRouter()
const teamData = inject(teamDataInjectionKey, ref())
const owner = computed(() => teamData.value?.role === 'OWNER')

const transferring = ref(false)
const disbanding = ref(false)
const leaving = ref(false)
const candidates = ref<TeamMember[]>([])
const candidatesLoading = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

async function openTransfer() {
  const team = teamData.value
  if (!team) return
  error.value = null
  candidates.value = []
  candidatesLoading.value = true
  try {
    const { members } = await TeamsApi.getMembers(team.id)
    candidates.value = members.filter((m) => m.user.id !== AccountService.user?.id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('home.nav.transferTeamLoadFailed')
  } finally {
    candidatesLoading.value = false
  }
}

/** 做成一件之前先清掉上一次的理由；做的时候按钮转圈。 */
async function run(action: (team: Team) => Promise<void>, failed: string) {
  const team = teamData.value
  if (!team || busy.value) return
  busy.value = true
  error.value = null
  try {
    await action(team)
  } catch (cause) {
    error.value = cause instanceof Error && cause.message ? cause.message : failed
  } finally {
    busy.value = false
  }
}

/** 这个团队不再是读者的了：侧栏那一行下去，项目清单跟着变，回待办。 */
async function forget(team: Team) {
  await patchQuery<Team[]>(keys.myTeams(), (rows) => rows.filter((row) => row.id !== team.id))
  void useWorkspaceStore().refreshProjects()
  await router.replace({ name: 'inbox' })
}

const transfer = (userId: number) =>
  run(async (team) => {
    const { team: updated } = await TeamsApi.transferOwner(team.id, userId)
    const mine = { ...team, ...updated, role: 'ADMIN' as const }
    teamData.value = mine
    await patchQuery<Team[]>(keys.myTeams(), (rows) => rows.map((row) => (row.id === mine.id ? mine : row)))
    transferring.value = false
  }, t('home.nav.transferTeamFailed'))

const disband = () =>
  run(async (team) => {
    await TeamsApi.del(team.id)
    disbanding.value = false
    toast.success(t('home.nav.disbandTeamDone', { name: team.name }))
    await forget(team)
  }, t('home.nav.disbandTeamFailed'))

const leave = () =>
  run(async (team) => {
    const userId = AccountService.user?.id
    if (typeof userId !== 'number') return
    await TeamsApi.removeMember(team.id, userId)
    leaving.value = false
    toast.success(t('home.nav.leaveTeamDone', { name: team.name }))
    await forget(team)
  }, t('home.nav.leaveTeamFailed'))
</script>

<template>
  <LeaveView
    v-if="teamData"
    v-model:transferring="transferring"
    v-model:disbanding="disbanding"
    v-model:leaving="leaving"
    :team="teamData"
    :owner="owner"
    :candidates="candidates"
    :candidates-loading="candidatesLoading"
    :busy="busy"
    :error="error"
    @open-transfer="openTransfer"
    @transfer="transfer"
    @disband="disband"
    @leave="leave"
  />
</template>
