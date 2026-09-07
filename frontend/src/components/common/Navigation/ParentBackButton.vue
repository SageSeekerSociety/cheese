<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const backTo = computed(() =>
  typeof route.meta.backTo === 'string' ? router.resolve({ name: route.meta.backTo }, route).path : null
)
</script>

<template>
  <v-btn v-if="backTo" :to="backTo" prepend-icon="mdi-arrow-left" color="on-surface" variant="text" size="small">
    返回上一级
  </v-btn>
</template>
