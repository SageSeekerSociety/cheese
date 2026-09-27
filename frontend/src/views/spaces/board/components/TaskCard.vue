<script setup lang="ts">
// 题目卡。板上列表、我的发布、我的领取三处共用，所以它必须**不说谎**：
// 状态标、领取进度、小队限制、截止时间都从同一份 task 派生，不各自算一遍。
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { type BoardTask, deadlineText, isOpen, STATE_LABEL } from '../model'
import { alreadyClaimed } from '../store'

const props = defineProps<{ task: BoardTask; showPublisher?: boolean }>()

const route = useRoute()
/** 详情在新外壳自己那棵树上（`/spaces/:id/board/tasks/:taskId`）。命名跳转要连
 *  空间 id 一起给。 */
const detailTo = computed(() => ({
  name: 'SpaceBoardTaskDetail',
  params: { spaceId: route.params.spaceId as string, taskId: props.task.id },
}))

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
  props.task.state === 'PUBLISHED' && !isOpen(props.task) ? '已截止' : STATE_LABEL[props.task.state]
)

const limitText = computed(() => {
  const t = props.task
  if (t.participantLimit === null) return `${t.claimCount} 人领取`
  return `${t.claimCount} / ${t.participantLimit} 人`
})

const full = computed(
  () => props.task.participantLimit !== null && props.task.claimCount >= props.task.participantLimit
)

const teamText = computed(() => {
  const { minTeamSize: lo, maxTeamSize: hi } = props.task
  if (lo === 1 && hi === 1) return '单人'
  return `小队 ${lo}–${hi} 人`
})

const claimed = computed(() => alreadyClaimed(props.task))
</script>

<template>
  <v-card flat rounded="lg" class="tcard" :class="{ 'tcard--dead': task.state === 'REJECTED' }" :to="detailTo">
    <div class="tcard__top">
      <v-chip size="x-small" label variant="tonal" :class="`tone-${stateTone}`">{{ stateText }}</v-chip>
      <v-chip v-if="task.category" size="x-small" label variant="text" class="tcard__cat">{{ task.category }}</v-chip>
      <v-spacer />
      <span v-if="claimed" class="tcard__mine">
        <v-icon icon="mdi-check-circle" size="14" />
        已领取
      </span>
    </div>

    <h3 class="tcard__title">{{ task.title }}</h3>
    <p class="tcard__summary">{{ task.summary }}</p>

    <div class="tcard__tags">
      <!-- 带讲解视频的题在列表里也看得出来，不用点进去才发现。 -->
      <v-chip v-if="task.videoUrl" size="x-small" label variant="tonal" class="tcard__video">
        <v-icon icon="mdi-play-circle-outline" size="13" start />
        视频
      </v-chip>
      <v-chip v-if="task.files?.length" size="x-small" label variant="tonal" class="tcard__files">
        <v-icon icon="mdi-paperclip" size="13" start />
        附件 {{ task.files.length }}
      </v-chip>
      <v-chip v-for="tag in task.tags" :key="tag" size="x-small" label variant="text" class="tcard__tag"
        >#{{ tag }}</v-chip
      >
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
