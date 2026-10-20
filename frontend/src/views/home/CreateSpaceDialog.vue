<script setup lang="ts">
// 新建空间。提交以后它是一份待平台管理员审核的申请，落到那份申请上。
//
// 换头像、问后端、成了往哪跳，都在 `composables/useTeamNav.ts`。这一件只收三样输入、
// 报上去（A 档）：`busy` 和「上一次为什么没成」由外面给。
import { ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 正在提交：按钮转圈，这时候关不掉。 */
  busy?: boolean
  /** 上一次没提交成的那一句。 */
  error?: string
}>()
const emit = defineEmits<{
  submit: [payload: { name: string; intro: string; avatarFile?: File }]
}>()

const open = defineModel<boolean>({ required: true })

const name = ref('')
const intro = ref('')
const avatarFile = ref<File>()

// 每次打开都从空开始：上一次取消留下的半截输入不该跟到下一次。
watch(open, (now) => {
  if (!now) return
  name.value = ''
  intro.value = ''
  avatarFile.value = undefined
})

function submit() {
  if (props.busy || !name.value.trim()) return
  emit('submit', { name: name.value, intro: intro.value, avatarFile: avatarFile.value })
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('spaces.create.open')"
    :primary-label="t('spaces.create.submit')"
    :primary-loading="busy"
    :primary-disabled="!name.trim() || busy"
    :close-disabled="busy"
    @primary="submit"
  >
    <v-form @submit.prevent="submit">
      <p class="t-body c-muted mb-4">{{ t('spaces.create.ownership') }}</p>
      <p class="t-body mb-2">{{ t('spaces.create.avatar') }}</p>
      <AvatarUploader v-if="open" v-model="avatarFile" :disabled="busy" class="mb-4 create-space__avatar" />
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the person opened this dialog to name a space; focus moves into that field -->
      <v-text-field
        v-model="name"
        autocomplete="off"
        maxlength="255"
        :counter="255"
        persistent-counter
        :label="t('spaces.create.name')"
        :placeholder="t('spaces.create.placeholder')"
        :disabled="busy"
        autofocus
        variant="outlined"
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
      <v-textarea
        v-model="intro"
        autocomplete="off"
        :label="t('spaces.create.intro')"
        :disabled="busy"
        rows="3"
        variant="outlined"
      />
      <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    </v-form>
  </AdaptiveDialog>
</template>

<style scoped>
.create-space__avatar {
  width: 120px;
}
</style>
