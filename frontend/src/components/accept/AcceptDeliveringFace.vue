<script setup lang="ts">
// 已采纳等合并 (`pr_open`, #718 退役): 历史卡的兜底脸，跟闸门那两张一个处理
// ——只读、不转圈（转圈是在说平台此刻正跑着什么，而平台什么也没跑）。采纳现在
// 当场合并，这个状态不会再有新卡进来。
import type { AcceptCard, PrChecks } from '@/cx_types'

import AcceptNoteLine from './AcceptNoteLine.vue'
import AcceptPrChecks from './AcceptPrChecks.vue'

import UserRef from '@/components/common/UserRefLink.vue'

defineProps<{
  card: AcceptCard
  checks: PrChecks | null
  /** 后端写在卡上的故障（唯一露头的地方），没有就不念。 */
  note: { text: string; tone: 'error' | 'info' } | null
}>()
</script>

<template>
  <v-card variant="outlined" class="merge-box">
    <div class="pa-3">
      <i18n-t
        scope="global"
        keypath="work.room.accept.deliveringBy"
        tag="div"
        class="text-caption text-medium-emphasis mb-2"
      >
        <template #who><UserRef :handle="card.decided_by" /></template>
      </i18n-t>
      <AcceptNoteLine v-if="note" :text="note.text" :tone="note.tone" />
      <!-- PR + 实时 CI，复用待采纳卡那套 prChecks 轮询。 -->
      <AcceptPrChecks v-if="card.pr_url" :card="card" :checks="checks" :sha="card.pr_head_sha" />
    </div>
  </v-card>
</template>

<style scoped src="./accept-card.css"></style>
