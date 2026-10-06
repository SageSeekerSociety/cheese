<script setup lang="ts">
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRef.vue'
import { t } from '@/i18n'

// 详情页右栏的「处理人」一格：谁领着这条，以及这个读者能不能领取 / 放弃。
//
// 两个按钮画不画全看服务端的 `canClaim` / `canRelease`（和领取那条路由同一处判据），
// 这里不自己判断「我是不是持有人」。按钮是中性描边：这一页的琥珀属于「支持」，领取
// 只对在做这个平台的那几个人出现，不是这一页让事情往下走的那一下。
//
// 人名那颗是纯展示的 `UserRef`（只认 props）：画成什么、点了去哪由容器算好的
// `resolveUser` 给全 —— 名册、当前项目、跳转都在 `useUserRefResolver` 里，这一格不该
// 自己去读路由。没传就画 handle、不可点（单测和 /demo 里没装路由照样画得出）。
const props = defineProps<{
  holder: string | null
  canClaim: boolean
  canRelease: boolean
  busy: boolean
  /** 这一个人叫什么、点了去哪。省略时画 handle、不可点。 */
  resolveUser?: (handle: string | null | undefined) => ResolvedUserRef
}>()

const emit = defineEmits<{ claim: []; release: []; navigate: [target: ResolvedUserRef['to']] }>()

function resolve(handle: string | null | undefined): ResolvedUserRef {
  return props.resolveUser?.(handle) ?? { name: handle ?? '', to: null }
}
</script>

<template>
  <div class="fb-claim">
    <div class="t-eyebrow mb-2">{{ t('feedback.detail.claim.title') }}</div>
    <div class="fb-claim__row">
      <span v-if="holder" class="t-meta-read fb-claim__who"
        ><UserRef
          :handle="holder"
          :name="resolve(holder).name"
          :to="resolve(holder).to"
          @navigate="emit('navigate', resolve(holder).to)"
      /></span>
      <span v-else class="t-meta-read fb-claim__who">{{ t('feedback.detail.claim.nobody') }}</span>
      <BaseButton v-if="canClaim" kind="secondary" size="sm" :loading="busy" @click="emit('claim')">
        {{ t('feedback.detail.claim.claim') }}
      </BaseButton>
      <BaseButton v-else-if="canRelease" kind="ghost" size="sm" :loading="busy" @click="emit('release')">
        {{ t('feedback.detail.claim.release') }}
      </BaseButton>
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
