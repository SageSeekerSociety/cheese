<script setup lang="ts">
// 团队设置的「基本信息」（`/teams/<地址>/profile`）：名称、头像、简介和团队地址。原先
// 前三样在侧栏 ⋯ 的「编辑团队资料」弹窗里，地址在成员页顶上；现在放在一处。这一页取数、
// 存，画法在同目录的 `ProfileView.vue`。
//
// 地址和其余几样分两次存：撞了的是名称还是地址，各说各的（两种都回 409）。换了地址就是
// 换了个地方，存好把浏览器带到新地址上，老地址不再通。
import type { Team } from '@/types'

import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import ProfileView from './ProfileView.vue'

import { ApiError } from '@/api'
import { createAvatar } from '@/api/avatars'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { patchQuery } from '@/query/client'
import { keys } from '@/query/keys'

const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
const addressPrefix = `${window.location.host}/teams/`

const router = useRouter()
const teamData = inject(teamDataInjectionKey, ref())

const canEdit = computed(() => teamData.value?.role === 'OWNER' || teamData.value?.role === 'ADMIN')
const saving = ref(false)
const saved = ref(false)
const error = ref('')
const nameError = ref('')
const handleError = ref('')

function edited() {
  saved.value = false
  error.value = ''
  nameError.value = ''
  handleError.value = ''
}

/** 侧栏那份团队名单跟着改：名字、头像、地址都画在那一行上。 */
async function remember(team: Team) {
  teamData.value = team
  await patchQuery<Team[]>(keys.myTeams(), (rows) =>
    rows.map((row) => (row.id === team.id ? { ...row, ...team } : row))
  )
}

async function save(payload: { name: string; intro: string; handle: string; avatarFile?: File }) {
  const team = teamData.value
  if (!team || saving.value) return
  edited()
  const name = payload.name.trim()
  if (!name) {
    nameError.value = t('work.teamProfile.nameRequired')
    return
  }
  if (payload.avatarFile && !AVATAR_TYPES.includes(payload.avatarFile.type)) {
    error.value = t('teams.settings.avatarHint')
    return
  }
  saving.value = true
  try {
    let current = team
    if (payload.handle !== team.handle) {
      try {
        current = (await TeamsApi.update(team.id, { handle: payload.handle })).team
      } catch (cause) {
        handleError.value =
          cause instanceof ApiError && cause.status === 409
            ? t('work.teamLink.handleTaken')
            : cause instanceof ApiError && cause.status === 400
              ? t('work.teamLink.handleInvalid')
              : t('work.teamProfile.saveFailed')
        return
      }
      await remember(current)
      await router.replace({ name: 'TeamsDetailProfile', params: { handle: current.handle } })
    }
    const avatarId = payload.avatarFile ? (await createAvatar(payload.avatarFile)).avatarId : null
    const { team: updated } = await TeamsApi.update(team.id, {
      name,
      intro: payload.intro.trim(),
      ...(avatarId ? { avatarId } : {}),
    })
    await remember(updated)
    saved.value = true
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) nameError.value = t('work.teamProfile.nameTaken')
    else if (cause instanceof ApiError && cause.status === 403) error.value = t('work.teamProfile.forbidden')
    else error.value = t('work.teamProfile.saveFailed')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ProfileView
    v-if="teamData"
    :team="teamData"
    :avatar="getAvatarUrl(teamData.avatarId)"
    :can-edit="canEdit"
    :address-prefix="addressPrefix"
    :avatar-accept="AVATAR_TYPES.join(',')"
    :saving="saving"
    :saved="saved"
    :error="error"
    :name-error="nameError"
    :handle-error="handleError"
    @save="save"
    @edited="edited"
  />
</template>
