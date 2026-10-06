<script setup lang="ts">
// What a reader sees when the route guard (`spaceManageGuard`) sends them here
// instead of a `/spaces/:spaceId/manage/*` page: the same shape as the project
// notice (`AccessNotice`) so the two read alike, but the copy is the space's
// own. The reader is usually a member who still belongs here, so the way out is
// back to the challenges, not out of the space.
//
// Props and events only — the route belongs to the page that renders this
// (scene rule: docs/manual/dev/scenes.md).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import AccessNotice from '@/components/common/AccessNotice.vue'

const props = defineProps<{
  /** The space whose management section was asked for. */
  spaceId: string
}>()

const { t } = useI18n()

const backTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: props.spaceId } }))
</script>

<template>
  <AccessNotice
    icon="mdi-shield-lock-outline"
    :title="t('spaces.manageDenied.title')"
    :body="t('spaces.manageDenied.body')"
  >
    <template #actions>
      <BaseButton kind="primary" :to="backTo">{{ t('spaces.manageDenied.backToTasks') }}</BaseButton>
    </template>
  </AccessNotice>
</template>
