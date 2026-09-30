<template>
  <v-form class="basic-info" @submit.prevent="submitUpdate">
    <div class="basic-info__avatar">
      <avatar-uploader v-model="selectedAvatar" :src="getAvatarUrl(space?.avatarId)" />
    </div>

    <div class="basic-info__fields">
      <v-text-field v-model="name" autocomplete="off" :label="t('spaces.settings.basic.name')" v-bind="nameProps" />
      <v-textarea
        v-model="intro"
        autocomplete="off"
        rows="3"
        auto-grow
        :counter="255"
        :label="t('spaces.settings.basic.intro')"
        v-bind="introProps"
      />

      <template v-if="isManager">
        <div class="basic-info__label t-body">{{ t('spaces.settings.basic.visibleLimit') }}</div>
        <v-radio-group v-model="visibleLimitMode" inline hide-details class="mb-2">
          <v-radio :label="t('spaces.settings.basic.visibleLimitUnlimited')" value="unlimited" />
          <v-radio :label="t('spaces.settings.basic.visibleLimitLimited')" value="limited" />
        </v-radio-group>
        <v-text-field
          v-model="visibleTaskLimitInput"
          type="number"
          min="0"
          step="1"
          :label="t('spaces.settings.basic.visibleLimitInput')"
          density="compact"
          variant="outlined"
          :disabled="visibleLimitMode === 'unlimited'"
          :error-messages="visibleTaskLimitError"
        />
      </template>

      <div class="basic-info__actions">
        <v-btn color="primary" variant="flat" :loading="saving" @click="submitUpdate">
          {{ t('spaces.settings.basic.save') }}
        </v-btn>
      </div>
    </div>
  </v-form>

  <!-- 危险区只对创建者可见：后端 delete_space 走的是 allow_admin=False 那道闸，
       管理员点下去只会拿到 403，摆一颗必然失败的按钮比不摆更糟。 -->
  <section v-if="isOwner" class="basic-info__danger">
    <h3 class="t-title c-danger">{{ t('spaces.detail.dangerZone') }}</h3>
    <p class="t-body c-muted mt-2 mb-4">{{ t('spaces.detail.deleteSpaceHint') }}</p>
    <v-btn color="error" variant="flat" :loading="isDeletingSpace" @click="confirmDeleteSpace">
      {{ t('spaces.detail.deleteSpace') }}
    </v-btn>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { storeToRefs } from 'pinia'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const router = useRouter()
const dialog = useDialog()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const { currentSpace: space, isOwner, isManager } = storeToRefs(spaceStore)

const { handleSubmit, defineField, resetForm } = useForm({
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
const saving = ref(false)

/** 表单跟着空间走：读回来、换了空间、保存后重新读回来，都照它重填一遍。 */
watch(
  space,
  (value) => {
    resetForm({ values: { name: value?.name ?? '', intro: value?.intro ?? '' } })
    visibleLimitMode.value = value?.visibleTaskLimit == null ? 'unlimited' : 'limited'
    visibleTaskLimitInput.value = String(value?.visibleTaskLimit ?? 0)
    visibleTaskLimitError.value = ''
    selectedAvatar.value = undefined
  },
  { immediate: true }
)

const parseVisibleTaskLimit = () => {
  visibleTaskLimitError.value = ''
  if (visibleLimitMode.value === 'unlimited') {
    return null
  }
  const raw = String(visibleTaskLimitInput.value).trim()
  if (!raw) {
    visibleTaskLimitError.value = t('spaces.settings.basic.visibleLimitRequired')
    return undefined
  }
  if (!/^\d+$/.test(raw)) {
    visibleTaskLimitError.value = t('spaces.settings.basic.visibleLimitInvalid')
    return undefined
  }
  return Number(raw)
}

const submitUpdate = handleSubmit(async (data) => {
  const current = space.value
  if (!current?.id) return
  const visibleTaskLimit = parseVisibleTaskLimit()
  if (visibleTaskLimit === undefined) return

  saving.value = true
  try {
    let avatarId = undefined
    if (selectedAvatar.value) {
      const { data: avatarData } = await AvatarsApi.createAvatar(selectedAvatar.value)
      avatarId = avatarData.avatarId
    }
    await SpacesApi.update(current.id, {
      name: data.name === current.name ? undefined : data.name,
      intro: data.intro,
      avatarId,
      ...(isManager.value ? { visibleTaskLimit } : {}),
    })
    toast.success(t('spaces.detail.updateSuccess'))
    // 读回来的空间会把表单重填一遍；失败时不读，填了一半的内容还在。
    await spaceStore.fetchSpace(current.id)
  } catch (error) {
    console.error('update space failed', error)
    toast.error(t('spaces.detail.updateFailed'))
  } finally {
    saving.value = false
  }
})

const isDeletingSpace = ref(false)

const confirmDeleteSpace = async () => {
  if (!space.value?.id) return
  const confirmed = await dialog
    .confirm(t('spaces.detail.confirmDeleteSpace', { name: space.value.name ?? '' }), {
      title: t('spaces.detail.deleteSpace'),
    })
    .wait()
  if (!confirmed) return

  isDeletingSpace.value = true
  try {
    await SpacesApi.del(space.value.id)
  } catch (error) {
    console.error('delete space failed', error)
    toast.error(t('spaces.detail.deleteSpaceFailed'))
    isDeletingSpace.value = false
    return
  }
  toast.success(t('spaces.detail.deleteSpaceSuccess'))
  // replace 而不是 push：这一页刚才还在的空间已经没了，「返回」不该把人送回它。
  void router.replace({ name: 'HomeSpaces' })
  isDeletingSpace.value = false
}
</script>

<style scoped>
.basic-info {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.basic-info__avatar {
  width: 160px;
}

.basic-info__fields {
  display: flex;
  flex: 1 1 320px;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.basic-info__label {
  color: var(--text);
}

.basic-info__actions {
  display: flex;
  justify-content: flex-end;
}

/* 危险区：红框红底，一眼看出这里跟上面那张表不是同一类操作 */
.basic-info__danger {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--danger);
  border-radius: var(--radius-md);
  background: var(--danger-wash);
}
</style>
