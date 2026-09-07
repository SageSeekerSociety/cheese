<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const backTo = computed(() =>
  typeof route.meta.backTo === 'string' ? router.resolve({ name: route.meta.backTo }, route).path : null
)
</script>

<template>
  <v-btn
    v-if="backTo"
    :to="backTo"
    icon
    color="on-surface-variant"
    variant="text"
    :size="mdAndUp ? 28 : 44"
    aria-label="返回上一级"
    title="返回上一级"
  >
    <v-icon size="20">mdi-arrow-left</v-icon>
  </v-btn>
</template>
