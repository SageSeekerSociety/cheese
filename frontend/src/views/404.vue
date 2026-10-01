<template>
  <v-container>
    <v-row>
      <v-col cols="12">
        <v-sheet flat rounded="lg">
          <v-empty-state
            title="404"
            :text="t('shell.notFound.text')"
            icon="mdi-alert-circle"
            :action-text="t('shell.notFound.home')"
            @click:action="goHome"
          >
          </v-empty-state>
        </v-sheet>
      </v-col>
    </v-row>
  </v-container>
</template>

<script lang="ts" setup>
import { useNavigation } from '@/composables/useNavigation'

import { t } from '@/i18n'

const nav = useNavigation()

const goHome = () => {
  nav?.navigate('/')
}
</script>
