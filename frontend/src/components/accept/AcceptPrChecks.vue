<script setup lang="ts">
// 采纳 PR 化 (#188 §5.1): 卡上的那个 PR 和它此刻的 CI。
//
// 待采纳的卡（人还没点）和交付中的卡（点完了，CI 在跑）骑的是同一个 PR，
// /pr-checks 对任何带 pr_number 的卡都答得上来，所以这一段两张脸共用。
//
// 只读：这里没有一行去打端点，检查状态由调用方（那份轮询）递进来。调用方负责
// 决定画不画这一段（没有 pr_url 就没有这一段）—— 这里的 `v-if` 是替它兜底：合成
// 一个类型收窄，`pr_url` 是 null 的时候那张 chip 就不画，而不是画成 `PR #null`。
import type { AcceptCard, PrChecks } from '@/cx_types'

import { t } from '@/i18n'

defineProps<{
  card: AcceptCard
  checks: PrChecks | null
  /** 交付中那张脸还会写自己是哪一版（PR head 的前 7 位）。 */
  sha?: string | null
}>()
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2">
      <v-chip
        v-if="card.pr_url"
        size="small"
        variant="tonal"
        prepend-icon="mdi-source-pull"
        :href="card.pr_url"
        target="_blank"
      >
        PR #{{ card.pr_number }}
      </v-chip>
      <span v-if="sha" class="text-caption text-medium-emphasis">
        {{ sha.slice(0, 7) }}
      </span>
      <span v-if="checks?.available && checks.mergeable === false" class="text-caption text-error">{{
        t('work.room.accept.conflictsWithMain')
      }}</span>
    </div>
    <div
      v-for="chk in checks?.checks ?? []"
      :key="chk.name"
      class="d-flex align-center ga-1 text-caption text-medium-emphasis mt-1"
    >
      <v-icon
        size="14"
        :color="chk.conclusion === 'success' ? 'success' : chk.conclusion === 'failure' ? 'error' : undefined"
      >
        {{
          chk.conclusion === 'success'
            ? 'mdi-check-circle'
            : chk.conclusion === 'failure'
              ? 'mdi-close-circle'
              : 'mdi-progress-clock'
        }}
      </v-icon>
      {{ chk.name }}
      <span v-if="chk.status !== 'completed'">{{ t('work.room.accept.checkRunning') }}</span>
    </div>
  </div>
</template>
