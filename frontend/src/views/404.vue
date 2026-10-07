<template>
  <v-container>
    <BaseEmptyState
      size="page"
      title="404"
      icon="mdi-alert-circle"
      :desc="t('shell.notFound.text')"
      :action="t('shell.notFound.home')"
      @action="goHome"
    />
  </v-container>
</template>

<script lang="ts" setup>
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { useNavigation } from '@/composables/useNavigation'

import { t } from '@/i18n'

const nav = useNavigation()

const goHome = () => {
  nav?.navigate('/')
}
</script>
