<template>
  <v-container>
    <v-row align="center" justify="center" class="pt-7 pb-7">
      <v-col cols="12" sm="8" md="6">
        <v-card class="text-center text-medium-emphasis" flat>
          <v-icon size="64">mdi-book-alert-outline</v-icon>
          <v-card-text>{{ t('shell.empty') }}</v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import { t } from '@/i18n'
</script>
