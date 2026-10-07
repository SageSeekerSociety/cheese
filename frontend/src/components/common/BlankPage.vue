<template>
  <v-container>
    <v-row align="center" justify="center" class="pt-7 pb-7">
      <v-col cols="12" sm="8" md="6">
        <BaseEmptyState size="page" icon="mdi-book-alert-outline" :title="t('shell.empty')" />
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'
</script>
