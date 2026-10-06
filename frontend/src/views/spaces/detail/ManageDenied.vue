<script setup lang="ts">
// Where the route guard (`spaceManageGuard`) sends anyone who is not this
// space's owner or admin and tried to open a `/spaces/:spaceId/manage/*` page
// directly. Before this, typing the address loaded the page anyway and the
// admin-only fetch answered 403 — the reader got a raw error box.
//
// The page keeps the one job a route page owns here — reading which space the
// address names — and hands the picture to `ManageDeniedView.vue`, which draws
// from props alone (scene rule: docs/manual/dev/scenes.md).
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import ManageDeniedView from './ManageDeniedView.vue'

defineOptions({ name: 'SpaceManageDenied' })

const route = useRoute()

const spaceId = computed(() => String(route.params.spaceId))
</script>

<template>
  <ManageDeniedView :space-id="spaceId" />
</template>
