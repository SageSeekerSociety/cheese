<template>
  <BasicInfoView
    :space="space"
    :is-manager="isManager"
    :is-owner="isOwner"
    :saving="saving"
    :deleting="deleting"
    @save="save"
    @delete="confirmDelete"
  />
</template>

<script setup lang="ts">
// 基本信息这一栏的容器：读写空间、删除后离开。画面在 `BasicInfoView.vue`。
import type { BasicInfoChange } from './BasicInfoView.vue'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import BasicInfoView from './BasicInfoView.vue'

import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const router = useRouter()
const dialog = useDialog()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space, isOwner, isManager } = storeToRefs(spaceStore)

const saving = ref(false)
const deleting = ref(false)

const save = async (change: BasicInfoChange) => {
  const current = space.value
  if (!current?.id) return
  saving.value = true
  try {
    let avatarId = undefined
    if (change.avatar) {
      const { data: avatarData } = await AvatarsApi.createAvatar(change.avatar)
      avatarId = avatarData.avatarId
    }
    await SpacesApi.update(current.id, {
      name: change.name === current.name ? undefined : change.name,
      intro: change.intro,
      avatarId,
      ...(change.visibleTaskLimit !== undefined ? { visibleTaskLimit: change.visibleTaskLimit } : {}),
    })
    toast.success(t('spaces.detail.updateSuccess'))
    // 读回来的空间会把表单重填一遍；失败时不读，填了一半的内容还在。
    await spaceData.fetchSpace(current.id)
  } catch (error) {
    console.error('update space failed', error)
    toast.error(t('spaces.detail.updateFailed'))
  } finally {
    saving.value = false
  }
}

const confirmDelete = async () => {
  if (!space.value?.id) return
  const confirmed = await dialog
    .confirm(t('spaces.detail.confirmDeleteSpace', { name: space.value.name ?? '' }), {
      title: t('spaces.detail.deleteSpace'),
      confirmLabel: t('spaces.detail.deleteSpace'),
      danger: true,
    })
    .wait()
  if (!confirmed) return

  deleting.value = true
  try {
    await SpacesApi.del(space.value.id)
  } catch (error) {
    console.error('delete space failed', error)
    toast.error(t('spaces.detail.deleteSpaceFailed'))
    deleting.value = false
    return
  }
  toast.success(t('spaces.detail.deleteSpaceSuccess'))
  // replace 而不是 push：这一页刚才还在的空间已经没了，「返回」不该把人送回它。
  void router.replace({ name: 'HomeSpaces' })
  deleting.value = false
}
</script>
