<script setup lang="ts">
import { ref } from 'vue'

import { t } from '@/i18n'

// A password field that can show what was typed. The caller says which kind of
// password it is; label and validation state pass straight through.
defineOptions({ inheritAttrs: false })

defineProps<{ autocomplete: 'current-password' | 'new-password' }>()

const model = defineModel<string>()
const visible = ref(false)
</script>

<template>
  <v-text-field v-model="model" v-bind="$attrs" :autocomplete="autocomplete" :type="visible ? 'text' : 'password'">
    <template #append-inner>
      <v-btn
        icon
        variant="text"
        color="medium-emphasis"
        size="small"
        density="comfortable"
        :aria-label="visible ? t('account.field.hidePassword') : t('account.field.showPassword')"
        :aria-pressed="visible"
        @click="visible = !visible"
      >
        <v-icon :icon="visible ? 'mdi-eye-off-outline' : 'mdi-eye-outline'" size="20" />
      </v-btn>
    </template>
  </v-text-field>
</template>
