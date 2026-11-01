<script setup lang="ts">
// 给项目自己的一个角色改名（#4795）。被拒不关窗，理由留在弹窗里。
import { ref, watch } from 'vue'

import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{ current: string; saving: boolean; error: string | null }>()
const emit = defineEmits<{ rename: [name: string] }>()
const open = defineModel<boolean>({ required: true })

const name = ref('')
watch(open, (v) => {
  if (v) name.value = props.current
})

function submit() {
  const next = name.value.trim()
  if (next) emit('rename', next)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.projectSettings.roles.rename')"
    :primary-label="t('work.projectSettings.roles.renameSubmit')"
    :primary-loading="saving"
    :primary-disabled="!name.trim()"
    :cancel-label="t('work.members.cancel')"
    :max-width="440"
    @primary="submit"
  >
    <BaseField :label="t('work.projectSettings.roles.nameLabel')" :error="error ?? undefined">
      <v-text-field
        v-model="name"
        autocomplete="off"
        variant="outlined"
        density="comfortable"
        hide-details
        maxlength="40"
        :aria-label="t('work.projectSettings.roles.nameLabel')"
        @keydown.enter="submit"
      />
    </BaseField>
  </AdaptiveDialog>
</template>
