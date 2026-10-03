<script setup lang="ts">
// 机器闸门 (eval C2, 已退役) 留下的那两张历史脸：检查未通过 / 检查没跑成。
//
// 采纳即合并 (#296, stage 1) 退役了机器闸门，今天递的卡不会再进任何闸门状态；
// 但库里退役之前的行还在，打开旧话题的人还要看得懂，所以这两张脸留着，只读。
//
// 两张刻意分开：一张是「代码红了」，一张是「检查本身没跑起来，对代码没有结论」
// —— 后者是需要人看一眼的状态，不是代码的问题。
import type { AcceptCard } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

defineProps<{
  /** 一定是 `gate_failed` 或 `gate_blocked`，别的状态这张脸不接。 */
  card: AcceptCard
  open: boolean
  agentName: string
  agentHandle: string | null
}>()

defineEmits<{ (e: 'update:open', open: boolean): void }>()
</script>

<template>
  <v-card variant="outlined" class="merge-box">
    <div class="pa-3">
      <!-- 闸门未过：卡片作废，芝士已被通知去修，修完会重新递卡。
           闸门没跑成：检查本身没能在门禁容器里跑起来，对代码没有结论。 -->
      <div class="text-caption text-medium-emphasis mb-2">
        <i18n-t
          scope="global"
          :keypath="
            card.status === 'gate_failed' ? 'work.room.accept.gateFailedBody' : 'work.room.accept.gateBlockedBody'
          "
          tag="span"
        >
          <template #agent><UserRef :handle="agentHandle" :name="agentName" /></template>
        </i18n-t>
      </div>
      <BaseButton
        kind="ghost"
        size="sm"
        :prepend-icon="open ? 'mdi-chevron-up' : 'mdi-chevron-down'"
        @click="$emit('update:open', !open)"
      >
        {{ open ? t('work.room.accept.hideGateOutput') : t('work.room.accept.showGateOutput') }}
      </BaseButton>
      <pre v-if="open" class="gate-output mt-2">{{ card.gate_output || t('work.room.accept.noGateOutput') }}</pre>
    </div>
  </v-card>
</template>

<style scoped>
/* 机器闸门: tail of the failed check's output (查看输出). */
.gate-output {
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>

<style scoped src="./accept-card.css"></style>
