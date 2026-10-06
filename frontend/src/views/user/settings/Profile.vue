<!--
  账号设置里的「个人资料」一页：这个人的记录、头像上传和保存请求都在这里，
  画面全在同目录的 ProfileView.vue（只吃 props 和事件）。改这里的是「从哪来、
  交给谁」，改样子去改视图。
-->
<template>
  <ProfileView
    :user="user"
    :shown-avatar="shownAvatar"
    :avatar-seed="avatarSeed"
    :can-remove-avatar="canRemoveAvatar"
    :changing-avatar="changingAvatar"
    :removing-avatar="removingAvatar"
    :saved-nickname="savedNickname"
    :saved-intro="savedIntro"
    :saving="saving"
    :saved="saved"
    :error="error"
    :avatar-accept="AVATAR_TYPES.join(',')"
    @save="save"
    @avatar-picked="onAvatarPicked"
    @remove-avatar="removeAvatar"
  />
</template>

<script setup lang="ts">
import type { User } from '@/types/users'

import { computed, onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, globalDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { useSaveState } from '@/composables/useSaveState'

import ProfileView from './ProfileView.vue'

import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import AccountService from '@/services/account'

// The server serves these four as images and anything else as an opaque file,
// which would show as a broken avatar. It sets no size limit of its own.
const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
const AVATAR_MAX_BYTES = 2 * 1024 * 1024

const fail = (error: unknown, fallback: string) => toast.error(requestErrorMessage(error, fallback))

const user = computed(() => AccountService.user)

/**
 * Takes a change the server accepted into the signed-in person's own record at
 * once, then refetches it so the copy kept for the next visit has it too.
 */
function remember(change: Partial<User>) {
  if (AccountService.user) AccountService.user = { ...AccountService.user, ...change }
  void AccountService.updateUserInfo()
}

// ---- Nickname and intro: edited in the view, saved together here ----

const savedNickname = computed(() => user.value?.nickname ?? '')
const savedIntro = computed(() => user.value?.intro ?? '')

// 保存结果就地回执（§3.11）：这一块一直在屏幕上，一条几秒就走掉的 toast
// 不够用。成功写进旁边那行 SaveStatus，失败同样是。脏没脏由视图自己比
// （草稿和它手里的已存值），这里只管保存这一件事。
const { saving, saved, error, run } = useSaveState({
  feedback: 'inline',
  messages: { saved: t('account.profile.saved'), failed: t('account.profile.saveFailed') },
})

async function save(change: { nickname: string; intro: string }) {
  const current = user.value
  if (!current) return
  await run(async () => {
    await UserApi.updateUserInfo(current.id, change)
    remember(change)
  })
}

// ---- Avatar: takes effect as soon as it is chosen ----

const changingAvatar = ref(false)
const removingAvatar = ref(false)

// Same seed as the avatar in the navigation, so both show the same initial.
const avatarSeed = computed(() => user.value?.nickname || String(user.value?.id ?? ''))
const shownAvatar = computed(() => (isChosenAvatar(user.value?.avatarId) ? getAvatarUrl(user.value?.avatarId) : ''))
// Removing an avatar puts the platform default back, which shows as the
// initial; until that default is known there is nothing to put back.
const canRemoveAvatar = computed(() => globalDefaultAvatarId.value !== null && isChosenAvatar(user.value?.avatarId))

async function onAvatarPicked(file: File) {
  if (!user.value) return
  if (!AVATAR_TYPES.includes(file.type)) {
    toast.error(t('account.profile.avatarWrongType'))
    return
  }
  if (file.size > AVATAR_MAX_BYTES) {
    toast.error(t('account.profile.avatarTooLarge'))
    return
  }
  changingAvatar.value = true
  try {
    const { data } = await AvatarsApi.createAvatar(file)
    await UserApi.updateUserInfo(user.value.id, { avatarId: data.avatarId })
    remember({ avatarId: data.avatarId })
  } catch (error) {
    fail(error, t('account.profile.avatarFailed'))
  } finally {
    changingAvatar.value = false
  }
}

async function removeAvatar() {
  const defaultId = globalDefaultAvatarId.value
  if (!user.value || defaultId === null) return
  removingAvatar.value = true
  try {
    await UserApi.updateUserInfo(user.value.id, { avatarId: defaultId })
    remember({ avatarId: defaultId })
  } catch (error) {
    fail(error, t('account.profile.removeAvatarFailed'))
  } finally {
    removingAvatar.value = false
  }
}

onMounted(ensureDefaultAvatarId)
</script>
