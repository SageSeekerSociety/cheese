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
  /** 上一次没提交成的那一句：这一步本身没成，多半是「稍后重试」这一档。 */
  error?: string
  /** 服务端给的原因，单独一行。它比上面那一句具体，但两句都要留着：上面那句说
   *  「这一步没成、可以再试」，这一句说「为什么」。没有回绝原因时为空。 */
  reason?: string | null
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
      <!-- 两行：上面是这一步没成，下面（有时）是服务端给的原因。分成两行而不是
           拼成一句，是因为它们回答两个不同的问题——「成没成、能不能再试」和
           「为什么」——只留一句时人会丢掉其中一半。 -->
      <v-alert v-if="error" type="error" variant="tonal" role="alert">
        <div>{{ error }}</div>
        <div v-if="reason" class="create-space__reason">{{ reason }}</div>
      </v-alert>
    </v-form>
  </AdaptiveDialog>
</template>

<style scoped>
.create-space__avatar {
  width: 120px;
}

/* 原因那一行：比上一行小一号、淡一档，读起来是「附注」而不是第二句结论。 */
.create-space__reason {
  margin-top: 4px;
  font-size: 0.875em;
  opacity: 0.85;
}
</style>
