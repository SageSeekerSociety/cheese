<!--
  ThemeToggle — the light/dark switch, shaped as one preference row of the user menu.

  Three states, not two: 跟随系统 / 浅色 / 深色. A plain on-off toggle cannot
  express "follow the OS", so the moment a user touches it they are pinned to
  whatever they picked and their machine switching at sunset stops reaching the
  app. `system` has to be a first-class, RE-selectable option, which is why this
  is a three-way control rather than a switch. State lives in src/theme.ts.
-->
<template>
  <div class="pref-row">
    <span class="pref-row__label">{{ t('navigation.userMenu.appearance') }}</span>
    <SegmentedControl
      :model-value="theme.preference.value"
      :options="options"
      :label="t('navigation.userMenu.appearance')"
      @update:model-value="theme.setPreference"
    />
  </div>
</template>

<script setup lang="ts">
import type { ThemePreference } from '@/theme'

import { computed } from 'vue'

import SegmentedControl from './SegmentedControl.vue'

import { t } from '@/i18n'
import { useAppTheme } from '@/theme'

const theme = useAppTheme()

// 名字按语言取，每一个都写全键名（而不是拼出来），文案目录的闸门才认得出它们在用。
const names = computed<Record<ThemePreference, string>>(() => ({
  system: t('navigation.userMenu.theme.system'),
  light: t('navigation.userMenu.theme.light'),
  dark: t('navigation.userMenu.theme.dark'),
}))
const options = computed(() => theme.options.map((value) => ({ value, label: names.value[value] })))
</script>
