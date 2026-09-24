<script setup lang="ts">
import { computed, ref } from 'vue'

import { t } from '@/i18n'

// A password field that can show what was typed, and says so while Caps Lock is
// on — the one reason a correct password gets rejected that the person cannot
// see. The caller says which kind of password it is; everything else passes
// straight through to the text field.
defineOptions({ inheritAttrs: false })

const props = defineProps<{
  autocomplete: 'current-password' | 'new-password'
  hint?: string
  persistentHint?: boolean
}>()

const model = defineModel<string>()
const visible = ref(false)
const capsLock = ref(false)

function readCapsLock(e: KeyboardEvent) {
  // Some keys (a lone modifier on some platforms) report no modifier state.
  if (typeof e.getModifierState === 'function') capsLock.value = e.getModifierState('CapsLock')
}

// The warning takes the hint's place for as long as it holds, then gives it back.
const hint = computed(() => (capsLock.value ? t('account.field.capsLock') : props.hint))
</script>

<template>
  <v-text-field
    v-model="model"
    v-bind="$attrs"
    :autocomplete="autocomplete"
    :type="visible ? 'text' : 'password'"
    :hint="hint"
    :persistent-hint="capsLock || persistentHint"
    @keydown="readCapsLock"
    @keyup="readCapsLock"
    @update:focused="(focused: boolean) => !focused && (capsLock = false)"
  >
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
