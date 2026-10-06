<script setup lang="ts">
// 侧栏一行右边那几个小头像：此刻在这个房间里干活的队友，和房间在等、等太久了的那
// 位成员。房间自己没有「在跑」「卡住了」这种状态，有的是某一位成员在做什么、在被
// 谁等着——所以画的是那位成员，悬停说出是谁、为什么。只凭 props 画。
import type { RailMemberMark } from '@/lib/memberActivity'

import { computed } from 'vue'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const props = defineProps<{ marks: RailMemberMark[] }>()

/** 一行里最多画几位；多出来的折成一个数字。 */
const MAX_SHOWN = 3

const shown = computed(() => props.marks.slice(0, MAX_SHOWN))
const more = computed(() => props.marks.length - shown.value.length)
const moreTitle = computed(() =>
  props.marks
    .slice(MAX_SHOWN)
    .map((m) => m.title)
    .join('\n')
)
</script>

<template>
  <span v-if="marks.length" class="rail-members">
    <span
      v-for="mark in shown"
      :key="mark.handle || mark.state"
      class="rail-members__one"
      :class="`rail-members__one--${mark.state}`"
      :data-state="mark.state"
      :title="mark.title"
      role="img"
      :aria-label="mark.title"
    >
      <CheeseAvatar v-if="mark.agent" :size="16" :name="mark.name" :handle="mark.handle || null" />
      <UserAvatar v-else :size="16" :name="mark.name" :avatar="mark.avatar ?? ''" />
      <span class="rail-members__dot" aria-hidden="true" />
    </span>
    <span v-if="more > 0" class="rail-members__more" :title="moreTitle">{{
      t('work.sidebar.moreMembers', { count: more })
    }}</span>
  </span>
</template>

<style scoped>
.rail-members {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex: none;
}
.rail-members__one {
  position: relative;
  display: inline-flex;
  width: 16px;
  height: 16px;
}
/* 右下角一颗小点说这位成员此刻怎么了：绿 = 在干活，红 = 房间在等它、等太久了。
   红点外面多一圈淡红晕，红绿色觉障碍下靠形状也分得开。描边取 --canvas：侧栏行
   坐在 --canvas 上，这一圈把点从头像上抠出来。 */
.rail-members__dot {
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  border: 1.5px solid var(--canvas);
}
.rail-members__one--working .rail-members__dot {
  background: var(--ok);
}
.rail-members__one--stalled .rail-members__dot {
  background: var(--danger);
  box-shadow: 0 0 0 2px var(--danger-wash);
}
.rail-members__more {
  font-size: 12px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
</style>
