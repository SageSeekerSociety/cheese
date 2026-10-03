<script setup lang="ts">
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

// 详情页右栏的「处理人」一格：谁领着这条，以及这个读者能不能领取 / 放弃。
//
// 两个按钮画不画全看服务端的 `canClaim` / `canRelease`（和领取那条路由同一处判据），
// 这里不自己判断「我是不是持有人」。按钮是中性描边：这一页的琥珀属于「支持」，领取
// 只对在做这个平台的那几个人出现，不是这一页让事情往下走的那一下。
defineProps<{
  holder: string | null
  canClaim: boolean
  canRelease: boolean
  busy: boolean
}>()

const emit = defineEmits<{ claim: []; release: [] }>()
</script>

<template>
  <div class="fb-claim">
    <div class="t-eyebrow mb-2">{{ t('feedback.detail.claim.title') }}</div>
    <div class="fb-claim__row">
      <span v-if="holder" class="t-meta-read fb-claim__who"><UserRef :handle="holder" /></span>
      <span v-else class="t-meta-read fb-claim__who">{{ t('feedback.detail.claim.nobody') }}</span>
      <v-btn v-if="canClaim" size="small" variant="outlined" color="secondary" :loading="busy" @click="emit('claim')">
        {{ t('feedback.detail.claim.claim') }}
      </v-btn>
      <v-btn
        v-else-if="canRelease"
        size="small"
        variant="text"
        color="secondary"
        :loading="busy"
        @click="emit('release')"
      >
        {{ t('feedback.detail.claim.release') }}
      </v-btn>
    </div>
  </div>
</template>

<style scoped>
.fb-claim__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
/* 长 handle 没有空格，不收住会把右栏撑宽。 */
.fb-claim__who {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
