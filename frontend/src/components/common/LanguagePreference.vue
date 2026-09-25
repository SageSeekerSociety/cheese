<!--
  界面语言，用户菜单里的一行偏好，和「外观」并排。登录后的顶栏不再放语言开关：
  它和外观一样是「我」的偏好，放在「我」的菜单里；未登录的页面仍由 LanguageToggle
  在页头提供。每个选项用它自己的语言写（LANGUAGE_NAMES），读不懂当前语言的人也
  认得出自己的那一个。
-->
<template>
  <div class="pref-row">
    <span class="pref-row__label">{{ t('navigation.userMenu.language') }}</span>
    <SegmentedControl
      :model-value="current"
      :options="options"
      :label="t('navigation.userMenu.language')"
      @update:model-value="setLocale"
    />
  </div>
</template>

<script setup lang="ts">
import type { Locale } from '@/i18n'

import { computed } from 'vue'

import SegmentedControl from './SegmentedControl.vue'

import i18n, { LANGUAGE_NAMES, setLocale, t } from '@/i18n'

const current = computed(() => i18n.global.locale.value as Locale)
const options = (Object.keys(LANGUAGE_NAMES) as Locale[]).map((locale) => ({
  value: locale,
  label: LANGUAGE_NAMES[locale],
  lang: locale,
}))
</script>
