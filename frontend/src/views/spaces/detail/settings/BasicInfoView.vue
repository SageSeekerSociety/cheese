<template>
  <form class="settings-card" novalidate @submit.prevent="submit">
    <div class="srow srow--field">
      <span class="srow__k">{{ t('spaces.settings.basic.avatar') }}</span>
      <div class="avatar-field">
        <img
          v-if="avatarPreview && !avatarBroken"
          class="avatar-field__img"
          :src="avatarPreview"
          alt=""
          @error="avatarBroken = true"
        />
        <span v-else class="avatar-field__img avatar-field__img--empty" aria-hidden="true">
          <v-icon size="24">mdi-image-outline</v-icon>
        </span>
        <BaseButton kind="secondary" @click="avatarInput?.click()">
          {{ t('spaces.settings.basic.changeAvatar') }}
        </BaseButton>
        <input
          ref="avatarInput"
          class="avatar-field__input"
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif"
          @change="onAvatarPicked"
        />
      </div>
    </div>

    <div class="srow srow--field">
      <label class="srow__k" for="space-name">{{ t('spaces.settings.basic.name') }}</label>
      <v-text-field
        id="space-name"
        v-model="name"
        autocomplete="off"
        variant="outlined"
        density="compact"
        hide-details="auto"
        v-bind="nameProps"
      />
    </div>

    <div class="srow srow--field">
      <label class="srow__k" for="space-intro">{{ t('spaces.settings.basic.intro') }}</label>
      <v-textarea
        id="space-intro"
        v-model="intro"
        autocomplete="off"
        variant="outlined"
        density="compact"
        rows="3"
        auto-grow
        :counter="255"
        persistent-counter
        v-bind="introProps"
      />
    </div>

    <div v-if="isManager" class="srow srow--field">
      <span class="srow__k srow__k--stack">
        {{ t('spaces.settings.basic.visibleLimitLabel') }}
        <small class="field-note">{{ t('spaces.settings.basic.visibleLimit') }}</small>
      </span>
      <div class="limit-field">
        <v-radio-group v-model="visibleLimitMode" inline hide-details density="compact">
          <v-radio :label="t('spaces.settings.basic.visibleLimitUnlimited')" value="unlimited" />
          <v-radio :label="t('spaces.settings.basic.visibleLimitLimited')" value="limited" />
        </v-radio-group>
        <v-text-field
          v-if="visibleLimitMode === 'limited'"
          v-model="visibleTaskLimitInput"
          class="limit-field__input"
          type="number"
          min="0"
          step="1"
          variant="outlined"
          density="compact"
          hide-details="auto"
          :aria-label="t('spaces.settings.basic.visibleLimitInput')"
          :error-messages="visibleTaskLimitError"
        />
      </div>
    </div>

    <div class="settings-foot">
      <BaseButton kind="primary" :loading="saving" @click="submit">
        {{ t('spaces.settings.basic.save') }}
      </BaseButton>
    </div>
  </form>

  <!-- 危险区只对创建者可见：后端 delete_space 走的是 allow_admin=False 那道闸，
       管理员点下去只会拿到 403，摆一颗必然失败的按钮比不摆更糟。 -->
  <section v-if="isOwner" class="settings-card danger">
    <div class="srow">
      <div class="danger__text">
        <span class="srow__k">{{ t('spaces.detail.deleteSpace') }}</span>
        <span class="field-note">{{ t('spaces.detail.deleteSpaceHint') }}</span>
      </div>
      <BaseButton kind="ghost" :loading="deleting" @click="emit('delete')">
        {{ t('spaces.detail.deleteSpace') }}
      </BaseButton>
    </div>
  </section>
</template>

<script setup lang="ts">
// 基本信息这一栏的画面：表单、校验和危险区。读写空间都不在这里 —— 容器
// `BasicInfo.vue` 把空间递进来，接住「保存」「删除」两件事去办。
import type { Space } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'

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
const avatarInput = ref<HTMLInputElement | null>(null)
const pickedPreview = ref<string>()
/** 头像地址读不出图时画占位，不画一个破图标。 */
const avatarBroken = ref(false)

/** 选了新头像先预览，保存时随表单一起交上去。 */
const avatarPreview = computed(
  () => pickedPreview.value ?? (props.space?.avatarId ? getAvatarUrl(props.space.avatarId) : '')
)

function onAvatarPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (pickedPreview.value) URL.revokeObjectURL(pickedPreview.value)
  selectedAvatar.value = file
  pickedPreview.value = URL.createObjectURL(file)
  avatarBroken.value = false
}
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
    if (pickedPreview.value) URL.revokeObjectURL(pickedPreview.value)
    pickedPreview.value = undefined
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

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.settings-card + .settings-card {
  margin-top: 24px;
}

/* 装控件的一行：标签对着控件的第一行。和个人设置同一种写法。 */
.srow--field {
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 20px 24px;
}

.srow--field > .srow__k {
  padding-top: 10px;
}

.srow__k--stack {
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.field-note {
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
  color: var(--muted);
}

.avatar-field {
  display: flex;
  gap: 16px;
  align-items: center;
}

.avatar-field__img {
  width: 56px;
  height: 56px;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.avatar-field__img--empty {
  display: grid;
  place-items: center;
  color: var(--faint);
  background: var(--fill-2);
}

.avatar-field__input {
  display: none;
}

.limit-field {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  padding-top: 2px;
}

.limit-field__input {
  max-width: 140px;
}

.danger .srow {
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 16px 24px;
}

.danger__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .srow--field {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .srow--field > .srow__k {
    padding-top: 0;
  }
}
</style>
