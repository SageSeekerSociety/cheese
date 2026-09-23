<script setup lang="ts">
// 题目卡。板上列表、我的发布、我的领取三处共用，所以它必须**不说谎**：
// 状态标、领取进度、小队限制、截止时间都从同一份 task 派生，不各自算一遍。
import { computed } from 'vue'

import { CLAIM_LABEL, STATE_LABEL, deadlineText, isOpen, type BoardTask } from '../fixtures'
import { alreadyClaimed, myClaim } from '../store'

const props = defineProps<{ task: BoardTask; showPublisher?: boolean }>()

const stateTone = computed(() => {
  switch (props.task.state) {
    case 'PUBLISHED':
      return isOpen(props.task) ? 'ok' : 'muted'
    case 'PENDING':
      return 'warn'
    case 'REJECTED':
      return 'danger'
    default:
      return 'muted'
  }
})

const stateText = computed(() =>
  props.task.state === 'PUBLISHED' && !isOpen(props.task) ? '已截止' : STATE_LABEL[props.task.state],
)

const limitText = computed(() => {
  const t = props.task
  const used = t.claims.length
  if (t.participantLimit === null) return `${used} 人领取`
  return `${used} / ${t.participantLimit} 人`
})

const full = computed(() => props.task.participantLimit !== null && props.task.claims.length >= props.task.participantLimit)

const teamText = computed(() => {
  const { minTeamSize: lo, maxTeamSize: hi } = props.task
  if (lo === 1 && hi === 1) return '单人'
  return `小队 ${lo}–${hi} 人`
})

const mine = computed(() => myClaim(props.task))
const claimed = computed(() => alreadyClaimed(props.task))
</script>

<template>
  <v-card flat rounded="lg" class="tcard" :class="{ 'tcard--dead': task.state === 'REJECTED' }" :to="`/task/${task.id}`">
    <div class="tcard__top">
      <v-chip size="x-small" label variant="tonal" :class="`tone-${stateTone}`">{{ stateText }}</v-chip>
      <v-chip size="x-small" label variant="text" class="tcard__cat">{{ task.category }}</v-chip>
      <v-spacer />
      <span v-if="claimed" class="tcard__mine">
        <v-icon icon="mdi-check-circle" size="14" />
        {{ mine ? CLAIM_LABEL[mine.status] : '已领取' }}
      </span>
    </div>

    <h3 class="tcard__title">{{ task.title }}</h3>
    <p class="tcard__summary">{{ task.summary }}</p>

    <div class="tcard__tags">
      <v-chip v-for="tag in task.tags" :key="tag" size="x-small" label variant="text" class="tcard__tag">#{{ tag }}</v-chip>
    </div>

    <div class="tcard__foot">
      <span v-if="showPublisher" class="tcard__pub">{{ task.publisher.name }}</span>
      <span class="tcard__stat" :class="{ 'is-full': full }">
        <v-icon icon="mdi-account-multiple-plus-outline" size="14" />
        {{ limitText }}
      </span>
      <span class="tcard__stat">
        <v-icon icon="mdi-account-multiple-outline" size="14" />
        {{ teamText }}
      </span>
      <v-spacer />
      <span class="tcard__stat" :class="{ 'is-due': task.state === 'PUBLISHED' && !isOpen(task) }">
        {{ deadlineText(task) }}
      </span>
    </div>
  </v-card>
</template>

<style scoped lang="scss">
.tcard {
  display: block;
  padding: 16px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgb(var(--v-theme-surface));
  transition: border-color 0.15s ease;
}

.tcard:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.tcard--dead {
  opacity: 0.7;
}

.tcard__top {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.tone-ok {
  color: rgb(var(--v-theme-success));
}

.tone-warn {
  color: rgb(var(--v-theme-warning));
}

.tone-danger {
  color: rgb(var(--v-theme-error));
}

.tone-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.tcard__cat {
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.tcard__mine {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: rgb(var(--v-theme-success));
  font-size: 0.74rem;
}

.tcard__title {
  margin: 0 0 6px;
  font-size: 0.98rem;
  font-weight: 600;
  line-height: 1.45;
}

.tcard__summary {
  display: -webkit-box;
  margin: 0 0 10px;
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.82rem;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.tcard__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 12px;
}

.tcard__tag {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.tcard__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  padding-top: 10px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.tcard__pub {
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.tcard__stat {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.is-full {
  color: rgb(var(--v-theme-warning));
}

.is-due {
  color: rgb(var(--v-theme-error));
}
</style>
