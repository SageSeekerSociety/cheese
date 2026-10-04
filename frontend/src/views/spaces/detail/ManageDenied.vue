<script setup lang="ts">
// Where the route guard (`spaceManageGuard`) sends anyone who is not this
// space's owner or admin and tried to open a `/spaces/:spaceId/manage/*` page
// directly. Before this, typing the address loaded the page anyway and the
// admin-only fetch answered 403 — the reader got a raw error box.
//
// Same shape as the project notice (`AccessNotice`) so the two read alike, but
// the copy is the space's own: the reader is usually a member who still belongs
// here, so the way out is back to the challenges, not out of the space.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import BaseButton from '@/components/base/BaseButton.vue'
import AccessNotice from '@/components/common/AccessNotice.vue'

defineOptions({ name: 'SpaceManageDenied' })

const { t } = useI18n()
const route = useRoute()

const spaceId = computed(() => String(route.params.spaceId))
const backTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))
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
