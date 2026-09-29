<template>
  <router-view />

  <v-dialog v-model="isEditingProfile" width="800">
    <v-card>
      <v-card-title>{{ t('spaces.detail.editSpaceInfo') }}</v-card-title>
      <v-card-text>
        <v-form>
          <v-container fluid>
            <v-row>
              <v-col cols="12" md="4">
                <avatar-uploader v-model="selectedAvatar" />
              </v-col>
              <v-col cols="12" md="8">
                <v-text-field
                  v-model="name"
                  autocomplete="off"
                  :label="t('spaces.detail.spaceName')"
                  v-bind="nameProps"
                />

                <v-list-subheader>{{ t('spaces.detail.intro') }}</v-list-subheader>
                <v-text-field v-model="intro" autocomplete="off" :counter="255" v-bind="introProps" />

                <template v-if="isCurrentUserAtLeastAdmin">
                  <v-list-subheader>每个发布者对普通用户可见的未结项已通过题目数量上限(M)</v-list-subheader>
                  <v-radio-group v-model="visibleLimitMode" inline hide-details class="mb-2">
                    <v-radio label="无限制" value="unlimited" />
                    <v-radio label="限制数量" value="limited" />
                  </v-radio-group>
                  <v-text-field
                    v-model="visibleTaskLimitInput"
                    type="number"
                    min="0"
                    step="1"
                    label="可见数量上限"
                    density="compact"
                    variant="outlined"
                    :disabled="visibleLimitMode === 'unlimited'"
                    :error-messages="visibleTaskLimitError"
                  />
                </template>
              </v-col>
            </v-row>
          </v-container>
        </v-form>

        <!-- 危险区只对创建者可见：后端 delete_space 走的是 allow_admin=False 那道闸，
             管理员点下去只会拿到 403，摆一颗必然失败的按钮比不摆更糟。 -->
        <template v-if="isCurrentUserOwner">
          <v-divider class="my-4" />
          <div class="edit-space-danger">
            <h3 class="t-title c-danger">{{ t('spaces.detail.dangerZone') }}</h3>
            <p class="t-body c-muted mt-2 mb-4">{{ t('spaces.detail.deleteSpaceHint') }}</p>
            <v-btn color="error" variant="flat" :loading="isDeletingSpace" @click="confirmDeleteSpace">
              {{ t('spaces.detail.deleteSpace') }}
            </v-btn>
          </div>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn color="primary" @click="closeUpdating">{{ t('spaces.detail.cancel') }}</v-btn>
        <v-btn color="primary" @click="submitUpdate">{{ t('spaces.detail.update') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="tsx" setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { storeToRefs } from 'pinia'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import { usePageTitle } from '@/composables/usePageTitle'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const { t } = useI18n()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const { closeEditProfile } = spaceStore
const {
  currentSpace: space,
  isEditingProfile,
  isOwner: isCurrentUserOwner,
  isManager: isCurrentUserAtLeastAdmin,
} = storeToRefs(spaceStore)

const { handleSubmit, defineField, handleReset, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      name: z.string().max(32),
      intro: z.string().max(255),
    })
  ),
})

const [name, nameProps] = defineField('name', vuetifyConfig)
const [intro, introProps] = defineField('intro', vuetifyConfig)
const selectedAvatar = ref<File>()
const visibleLimitMode = ref<'limited' | 'unlimited'>('unlimited')
const visibleTaskLimitInput = ref<string | number>('0')
const visibleTaskLimitError = ref('')

const closeUpdating = () => {
  closeEditProfile()
  selectedAvatar.value = undefined
  handleReset()
}

const getSpace = async (spaceId: number) => {
  await spaceStore.fetchSpace(spaceId)
  spaceStore.fetchCategories()
  resetForm({
    values: {
      name: space.value?.name,
      intro: space.value?.intro,
    },
  })
  if (space.value?.name) {
    setDynamicTitle(space.value?.name, 'SpacesDetail')
  }
  visibleLimitMode.value = space.value?.visibleTaskLimit == null ? 'unlimited' : 'limited'
  visibleTaskLimitInput.value = String(space.value?.visibleTaskLimit ?? 0)
  visibleTaskLimitError.value = ''
}

onMounted(async () => {
  await getSpace(Number(route.params.spaceId))
})

onBeforeRouteUpdate(async (to, from) => {
  if (to.params.spaceId !== from.params.spaceId) {
    await getSpace(Number(to.params.spaceId))
  }
})

const submitUpdate = handleSubmit(async (data) => {
  if (!space.value?.id) {
    return
  }
  try {
    let avatarId = undefined
    if (selectedAvatar.value) {
      const { data: avatarData } = await AvatarsApi.createAvatar(selectedAvatar.value)
      avatarId = avatarData.avatarId
    }
    const visibleTaskLimit = parseVisibleTaskLimit()
    if (visibleTaskLimit === undefined) {
      return
    }
    await SpacesApi.update(space.value.id, {
      name: data.name === space.value.name ? undefined : data.name,
      intro: data.intro,
      avatarId,
      ...(isCurrentUserAtLeastAdmin.value ? { visibleTaskLimit } : {}),
    })
    closeUpdating()
    toast.success(t('spaces.detail.updateSuccess'))
  } catch (error) {
    toast.error(t('spaces.detail.updateFailed'))
  } finally {
    await getSpace(Number(route.params.spaceId))
  }
})

const isDeletingSpace = ref(false)

const confirmDeleteSpace = async () => {
  if (!space.value?.id) {
    return
  }
  const confirmed = await dialog
    .confirm(t('spaces.detail.confirmDeleteSpace', { name: space.value.name ?? '' }), {
      title: t('spaces.detail.deleteSpace'),
    })
    .wait()
  if (!confirmed) {
    return
  }

  isDeletingSpace.value = true
  try {
    await SpacesApi.del(space.value.id)
  } catch (error) {
    console.error('删除题目板失败:', error)
    toast.error(t('spaces.detail.deleteSpaceFailed'))
    isDeletingSpace.value = false
    return
  }
  toast.success(t('spaces.detail.deleteSpaceSuccess'))
  closeEditProfile()
  // replace 而不是 push：这一页刚才还在的题目板已经没了，「返回」不该把人送回它。
  void router.replace({ name: 'HomeSpaces' })
  isDeletingSpace.value = false
}

const parseVisibleTaskLimit = () => {
  visibleTaskLimitError.value = ''
  if (visibleLimitMode.value === 'unlimited') {
    return null
  }
  const raw = String(visibleTaskLimitInput.value).trim()
  if (!raw) {
    visibleTaskLimitError.value = '请输入整数 M'
    return undefined
  }
  if (!/^\d+$/.test(raw)) {
    visibleTaskLimitError.value = '仅允许输入大于等于 0 的整数'
    return undefined
  }
  return Number(raw)
}
</script>

<style scoped lang="scss">
/* 编辑弹窗底部的危险区：红框红底，一眼看出这里跟上面几栏不是同一类操作 */
.edit-space-danger {
  border: 1px solid var(--danger);
  padding: 16px;
  background: var(--danger-wash);
  border-radius: 8px;
}
</style>
