<template>
  <BaseEmptyState
    icon="mdi-alert-circle"
    title="404"
    :desc="t('shell.notFound.text')"
    :action="t('shell.notFound.home')"
    @action="goHome"
  />
</template>

<script lang="ts" setup>
import { useNavigation } from '@/composables/useNavigation'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

const nav = useNavigation()

const goHome = () => {
  nav?.navigate('/')
}
</script>
