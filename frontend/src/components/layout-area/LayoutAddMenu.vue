<script setup lang="ts">
/** The "+" of a layout area: the panels that are in no cell, to bring one back. */
import type { LayoutPanelInfo } from './types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ panels: LayoutPanelInfo[] }>()
const emit = defineEmits<{ pick: [id: string] }>()

const { t } = useI18n()
</script>

<template>
  <v-menu location="bottom end">
    <template #activator="{ props: activator }">
      <BaseButton
        v-bind="activator"
        kind="ghost"
        size="sm"
        icon="mdi-plus"
        :aria-label="t('shell.layout.add')"
        :title="t('shell.layout.add')"
        data-layout-add
      />
    </template>
    <v-list density="compact">
      <v-list-item
        v-for="panel in panels"
        :key="panel.id"
        :prepend-icon="panel.icon"
        :title="panel.title"
        :data-layout-add-item="panel.id"
        @click="emit('pick', panel.id)"
      />
    </v-list>
  </v-menu>
</template>
