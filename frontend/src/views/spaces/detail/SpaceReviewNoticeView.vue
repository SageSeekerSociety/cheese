<script setup lang="ts">
// What a space's owner sees on their own space before it has passed review.
//
// Until a platform admin approves it, the backend answers 404 for everything
// under the space but its own detail (`require_reviewed_space`), so the pages
// inside it have nothing to show: they used to mount anyway and each flash a red
// "failed to load" toast. This screen takes their place and says where the
// space stands. The way out is the 空间 page, where 「我的空间申请」 lists the
// application and, once rejected, the button to resubmit it.
//
// Props only — the shell (`views/spaces/Detail.vue`) decides when to show it.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import AccessNotice from '@/components/common/AccessNotice.vue'

const props = defineProps<{
  status: 'PENDING' | 'REJECTED'
  /** The reviewer's reason, when the application was rejected. */
  reason?: string | null
}>()

const { t } = useI18n()

const said = computed(() =>
  props.status === 'REJECTED'
    ? {
        icon: 'mdi-close-octagon-outline',
        title: t('spaces.reviewNotice.rejectedTitle'),
        body: props.reason
          ? t('spaces.reviewNotice.rejectedBodyReason', { reason: props.reason })
          : t('spaces.reviewNotice.rejectedBody'),
      }
    : {
        icon: 'mdi-timer-sand',
        title: t('spaces.reviewNotice.pendingTitle'),
        body: t('spaces.reviewNotice.pendingBody'),
      }
)
</script>

<template>
  <AccessNotice :icon="said.icon" :title="said.title" :body="said.body">
    <template #actions>
      <BaseButton kind="primary" :to="{ name: 'HomeSpaces' }">{{ t('spaces.reviewNotice.backToSpaces') }}</BaseButton>
    </template>
  </AccessNotice>
</template>
