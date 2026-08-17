<!--
  ThemeToggle — the light/dark switch, shaped as one row of a user menu.

  Three states, not two: 跟随系统 / 浅色 / 深色. A plain on-off toggle cannot
  express "follow the OS", so the moment a user touches it they are pinned to
  whatever they picked and their machine switching at sunset stops reaching the
  app. `system` has to be a first-class, RE-selectable option, which is why this
  is a three-way control rather than a switch.

  Self-contained on purpose: it is dropped into existing menus with a single
  line so the surrounding files barely change. State lives in src/theme.ts.
-->
<template>
  <v-list-item rounded="lg" class="mb-1 theme-toggle">
    <template #prepend>
      <v-icon icon="mdi-theme-light-dark" class="me-2"></v-icon>
    </template>
    <v-list-item-title>外观</v-list-item-title>
    <template #append>
      <!-- .stop so picking a theme does not bubble up and close the menu the
           control lives in — you usually want to try both and compare. -->
      <v-btn-toggle
        :model-value="theme.preference.value"
        density="compact"
        variant="outlined"
        divided
        mandatory
        class="theme-toggle__group"
        @click.stop
        @update:model-value="theme.setPreference"
      >
        <v-btn
          v-for="option in theme.options"
          :key="option.value"
          v-tooltip="option.label"
          :value="option.value"
          :aria-label="option.label"
          size="small"
          class="px-2"
        >
          <v-icon :icon="option.icon" size="small"></v-icon>
        </v-btn>
      </v-btn-toggle>
    </template>
  </v-list-item>
</template>

<script setup lang="ts">
import { useAppTheme } from '@/theme'

const theme = useAppTheme()
</script>

<style scoped>
/* The buttons inherit VBtn's 8px default radius from the Vuetify defaults;
   only the group's overall size needs constraining so the row stays one line. */
.theme-toggle__group {
  height: 28px;
}
</style>
