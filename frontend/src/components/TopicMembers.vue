<script setup lang="ts">
// 频道里有谁：页头一小串头像。点它打开频道详情的「成员」那一页（TopicRoster），名册
// 本身、加人移人、每位 AI 队友的环境都在那里。
//
// 管这个频道的人叫什么也从这里报出去：频道详情的「关于」里写它，名册是唯一记着它的地方。
import { computed, watch } from 'vue'

import { useTopicRoster } from '../composables/useTopicRoster'
import { t } from '../i18n'
import { memberName } from '../lib/agentNames'
import { getAvatarUrl } from '../utils/materials'

import UserAvatar from './common/UserAvatar.vue'
import CheeseAvatar from './CheeseAvatar.vue'

const props = defineProps<{ topicId: string }>()
const emit = defineEmits<{
  /** 点了这一串头像。 */
  (e: 'open'): void
  (e: 'manager', name: string | null): void
}>()

const { members } = useTopicRoster(() => props.topicId)

watch(
  members,
  (rows) => {
    const owner = rows.find((m) => m.role === 'owner' && !m.agent)
    emit('manager', owner ? memberName(owner) || owner.member_handle : null)
  },
  { immediate: true }
)

// 一份名册：AI 队友就是其中一位，不在人数外面再挂一个。「位」而不是「人」：同一句话
// 要数得下一个 AI 队友。
const countLabel = computed(() => t('work.room.roster.count', { count: members.value.length }))

// Compact indicator: the first few faces as a stack, capped so the stack never
// grows unbounded; the rest fold into "+N".
const MAX_FACES = 3
const stackFaces = computed(() => members.value.slice(0, MAX_FACES))
const overflow = computed(() => Math.max(0, members.value.length - MAX_FACES))
</script>

<template>
  <button
    type="button"
    class="members-mini tap-target"
    :title="t('global.labelWithAside', { label: t('work.room.roster.title'), aside: countLabel })"
    data-testid="channel-members"
    @click="emit('open')"
  >
    <span class="members-mini__stack">
      <template v-for="(m, i) in stackFaces" :key="m.member_handle">
        <CheeseAvatar
          v-if="m.agent"
          class="members-mini__ai"
          :size="22"
          :name="memberName(m) || m.member_handle"
          :handle="m.member_handle"
          :style="{ zIndex: MAX_FACES - i }"
        />
        <UserAvatar
          v-else
          class="members-mini__face"
          :size="22"
          :name="memberName(m) || m.member_handle"
          :seed="m.member_handle"
          :avatar="m.avatar_id != null ? getAvatarUrl(m.avatar_id) : ''"
          :style="{ zIndex: MAX_FACES - i }"
        />
      </template>
      <span v-if="overflow" class="members-mini__face members-mini__face--more" :style="{ zIndex: 0 }"
        >+{{ overflow }}</span
      >
    </span>
  </button>
</template>

<style scoped>
/* Compact roster indicator: an avatar stack + count, no full-width bar.
   Sits at the top-right of the topic/chat header row (fusion-design §3). */
/* 相对定位给 .tap-target：这一颗只有 28px 高，手机顶栏里手指要点得中。 */
.members-mini {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px 0 5px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
  transition:
    background 0.12s ease,
    border-color 0.12s ease;
}
.members-mini:hover {
  background: var(--fill);
  border-color: var(--line-2);
}
/* 按下那一刻就变色：触屏上没有悬停，面板又要等一下才出来，不变一下就像没点到。 */
.members-mini:active {
  background: var(--fill);
  border-color: var(--line-2);
}
.members-mini__stack {
  display: inline-flex;
  align-items: center;
}
.members-mini__face {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  margin-left: -7px;
  font-size: 0.66rem;
  font-weight: 700;
  /* 用 --avatar-ink 而不是 var(--ink)：底色由 UserAvatar 按 seed（member_handle）
     用 avatarColor() 算出来，两套主题下是同一个值，压在上面的字也得是同一个值。
     底色逐人不同 —— 一排脸共用一个颜色就等于没有脸，而这一排存在的意义正是彼此不同。 */
  color: var(--avatar-ink);
  border: 1.5px solid var(--surface);
  box-sizing: border-box;
  overflow: hidden;
}
.members-mini__face:first-child {
  margin-left: 0;
}
.members-mini__face--more {
  /* 不是一张脸，是「还有几个人」—— 用界面的填充色，别混进彩色头像里。 */
  background: var(--fill-2);
  color: var(--muted);
  font-size: 0.6rem;
}
/* AI 队友在头像堆里和在别处一个样子（CheeseAvatar）。叠在一起时和人的头像一样
   描一圈底色，前后两张脸才分得开：描边压在超椭圆的边上，约 1.5px。 */
.members-mini__ai {
  margin-left: -7px;
}
.members-mini__ai:first-child {
  margin-left: 0;
}
.members-mini__ai :deep(.cheese-avatar__tile) {
  stroke: var(--surface);
  stroke-width: 14px;
}
</style>
