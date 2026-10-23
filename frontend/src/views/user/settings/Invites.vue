<script setup lang="ts">
/**
 * 「邀请好友」（`/users/settings/invites`）。读自己的邀请码、复制码和注册链接在这里，
 * 画法在同目录的 `InvitesView.vue`——这一页只做它的容器。码在第一次读时由服务端建出来。
 */
import type { MyInvites } from '@/types/invites'
import type { InviteeRow } from './InvitesView.vue'

import { computed, onMounted, ref } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'

import InvitesView from './InvitesView.vue'

import { getMyInvites, inviteSignupLink } from '@/api/invites'
import { copyLink, copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { myHandle } from '@/me'

const invites = ref<MyInvites | null>(null)
const failed = ref(false)
const failingReason = ref<string | null>(null)

async function load() {
  failed.value = false
  failingReason.value = null
  try {
    invites.value = await getMyInvites()
  } catch (e) {
    failed.value = true
    failingReason.value = e instanceof Error ? e.message : null
  }
}

onMounted(load)

// 只画好友**自己挑过**的头像：每个新账号都带着同一张默认头像（见 layouts/user/Settings.vue）。
ensureDefaultAvatarId()
const people = computed<InviteeRow[]>(() =>
  (invites.value?.invitees ?? []).map((person) => ({
    handle: person.handle,
    nickname: person.nickname,
    avatarUrl: isChosenAvatar(person.avatar_id) ? getAvatarUrl(person.avatar_id) : '',
    registeredAt: person.registered_at,
    stage: person.stage,
    credits: person.credits,
  }))
)

/** 个人额度在自己那支个人团队的「额度」页。 */
const creditsTo = computed(() => {
  const handle = myHandle()
  return handle ? { name: 'TeamsDetailCredits', params: { handle } } : null
})

function copyCode() {
  if (invites.value) void copyText(invites.value.code, t('account.invites.code.copied'))
}

function copySignupLink() {
  if (invites.value) void copyLink(inviteSignupLink(invites.value.code))
}
</script>

<template>
  <InvitesView
    :invites="invites"
    :people="people"
    :failed="failed"
    :failing-reason="failingReason"
    :credits-to="creditsTo"
    @copy-code="copyCode"
    @copy-link="copySignupLink"
    @retry="load"
  />
</template>
