<script setup lang="ts">
import { ref } from 'vue'

import { t } from '@/i18n'

// A password field that can show what was typed, and says so while Caps Lock is
// on — the one reason a correct password gets rejected that the person cannot
// see. The caller says which kind of password it is; everything else passes
// straight through to the text field.
//
// The warning sits inside the box, not under it: the browser's and password
// managers' suggestion lists open right below a focused password field and
// cover anything there.
defineOptions({ inheritAttrs: false })

defineProps<{ autocomplete: 'current-password' | 'new-password' }>()

const model = defineModel<string>()
const visible = ref(false)
const capsLock = ref(false)

// Keyboard and mouse events both carry the modifier state, so a click into the
// field already knows, before the first character goes in wrong.
function readCapsLock(e: KeyboardEvent | MouseEvent) {
  if (typeof e.getModifierState === 'function') capsLock.value = e.getModifierState('CapsLock')
}

// Pressing Caps Lock itself is where platforms disagree: some report the state
// from before the press on its keydown, and macOS sends only a keydown when it
// turns on and only a keyup when it turns off. Flipping on the keydown and
// reading on the keyup is right on all of them.
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'CapsLock') capsLock.value = !capsLock.value
  else readCapsLock(e)
}
</script>

<template>
  <v-text-field
    v-model="model"
    v-bind="$attrs"
    :autocomplete="autocomplete"
    :type="visible ? 'text' : 'password'"
    @keydown="onKeydown"
    @keyup="readCapsLock"
    @mousedown:control="readCapsLock"
    @update:focused="(focused: boolean) => !focused && (capsLock = false)"
  >
    <template #append-inner>
      <!-- Present from the start, so a screen reader announces the change. -->
      <span class="password-field__status" role="status">{{ capsLock ? t('account.field.capsLock') : '' }}</span>
      <span v-if="capsLock" class="password-field__caps" aria-hidden="true" :title="t('account.field.capsLock')">
        <v-icon icon="mdi-apple-keyboard-caps" size="14" />
        {{ t('account.field.capsLockShort') }}
      </span>
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

<style scoped>
.password-field__status {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.password-field__caps {
  display: inline-flex;
  flex: none;
  gap: 2px;
  align-items: center;
  padding: 1px 6px;
  margin-right: 2px;
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  color: var(--warn-ink);
  white-space: nowrap;
  background: var(--warn-wash);
  border-radius: var(--radius-sm);
}
</style>
