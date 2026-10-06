<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.detail.selectTemplate.title')" color="transparent" density="compact">
      <template #prepend>
        <BaseButton kind="ghost" prepend-icon="mdi-chevron-left" @click="emit('back')">{{
          t('spaces.detail.selectTemplate.back')
        }}</BaseButton>
      </template>
    </v-toolbar>

    <v-list rounded="lg">
      <v-list-item
        prepend-icon="mdi-file-outline"
        :title="t('spaces.detail.selectTemplate.blankTemplate')"
        @click="emit('select', null)"
      ></v-list-item>
      <v-list-item
        v-for="(template, index) in templates"
        :key="index"
        :title="template.name"
        :subtitle="template.description"
        @click="emit('select', index)"
      >
        <template #prepend>
          <v-icon>mdi-file-document-outline</v-icon>
        </template>
      </v-list-item>
    </v-list>
  </v-sheet>
</template>

<script setup lang="ts">
// 选模板这一屏的画面：一张空白项加一列模板。往后走（去发布页）归容器
// `SelectTemplate.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceTaskTemplate } from '@/types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ templates: SpaceTaskTemplate[] }>()

// select 给的是模板在这一列里的下标；null 是那张空白项。
const emit = defineEmits<{ back: []; select: [index: number | null] }>()

const { t } = useI18n()
</script>
