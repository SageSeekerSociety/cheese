<template>
  <v-form class="basic-info" @submit.prevent="submit">
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
        <v-btn color="primary" variant="flat" :loading="saving" @click="submit">
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
    <v-btn color="error" variant="flat" :loading="deleting" @click="emit('delete')">
      {{ t('spaces.detail.deleteSpace') }}
    </v-btn>
  </section>
</template>

<script setup lang="ts">
// 基本信息这一栏的画面：表单、校验和危险区。读写空间都不在这里 —— 容器
// `BasicInfo.vue` 把空间递进来，接住「保存」「删除」两件事去办。
import type { Space } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import AvatarUploader from '@/components/common/AvatarUploader.vue'

export interface BasicInfoChange {
  name: string
  intro: string
  avatar?: File
  /** 只有管理员改得了；`null` 是不限，缺省是这次不改。 */
  visibleTaskLimit?: number | null
}

const props = defineProps<{
  space: Space | null
  isManager: boolean
  isOwner: boolean
  saving: boolean
  deleting: boolean
}>()

const emit = defineEmits<{
  save: [change: BasicInfoChange]
  delete: []
}>()

const { t } = useI18n()

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

/** 表单跟着空间走：读回来、换了空间、保存后重新读回来，都照它重填一遍。 */
watch(
  () => props.space,
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

const submit = handleSubmit((data) => {
  if (!props.space) return
  const change: BasicInfoChange = { name: data.name, intro: data.intro, avatar: selectedAvatar.value }
  if (props.isManager) {
    const visibleTaskLimit = parseVisibleTaskLimit()
    if (visibleTaskLimit === undefined) return
    change.visibleTaskLimit = visibleTaskLimit
  }
  emit('save', change)
})
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
