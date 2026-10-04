<script setup lang="ts">
// 时间线上「谁做了一件事」那一行的外框（改了几个文件、记了一条决策、检查没过、
// 工作电脑就绪）。
//
// 记号回答「这是谁的事」。某个 AI 队友的事（它那一轮的正在处理、失败、重试、平台
// 替它处理好的那几行）就以它的身份出现，和它发的消息一模一样：同样大的头像，名字
// 后面跟时间，读起来是它自己说的——头像小一号、时间挪到行尾，一眼就看得出这是另一种
// 东西。平台自己的事（不属于任何一位队友的那一轮）是头像列里一个小一号的中性记号，
// 事件那一行接在后面，时间在行尾。两种的正文都和消息正文在同一条竖线上。轻重不归
// 记号管，归这一行的底色（RoomNotice 的 `sys-row--warn/danger`）。
import type { FaceState } from '../lib/agentFace'

import { computed } from 'vue'

import CheeseAvatar from './CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{
  name: string | null
  handle?: string | null
  time: string
  cont?: boolean
  /** 这一行的头像是这位队友最近出现的那个，它正在干活（或刚干完）：头像的表情。 */
  face?: FaceState | null
  /** 头像在动时，悬停看到的那一句（现场顶上那一行）。有它，头像点下去去现场。 */
  faceLabel?: string | null
  /** 头像在动时，读屏读到的那一句：只有状态，不带在走的秒数，免得每秒再念一遍。 */
  faceStatus?: string | null
}>()

const liveLabel = computed(() =>
  props.faceLabel ? t('work.agentAvatar.live', { name: props.name, status: props.faceLabel }) : null
)
const liveName = computed(() =>
  props.faceStatus ? t('work.agentAvatar.live', { name: props.name, status: props.faceStatus }) : undefined
)
</script>

<template>
  <div v-if="name" class="notice-row notice-row--agent agent-status" :class="{ 'notice-row--cont': cont }">
    <!-- 续行和消息的续话一样：头像列空着，时间在悬停时出现在那里。 -->
    <span v-if="cont" class="notice-row__gutter">
      <span class="notice-row__gutter-time">{{ time }}</span>
    </span>
    <!-- 在动的头像和消息里的一样：点下去去现场（ChatPanel 认 data-site）。 -->
    <button
      v-else-if="liveLabel"
      type="button"
      class="notice-row__avatar notice-row__live im-person"
      data-site=""
      :aria-label="liveName"
    >
      <!-- 这一句带着每秒在走的秒数：不用原生 title（一换字就收起再弹，悬停时每秒
           闪一下），用原地换字的气泡。 -->
      <v-tooltip activator="parent" location="top" :text="liveLabel" />
      <CheeseAvatar :size="28" :name="name" :handle="handle" :state="face ?? null" />
    </button>
    <span v-else class="notice-row__avatar" :title="name" :aria-label="name" role="img">
      <CheeseAvatar :size="28" :name="name" :handle="handle" :state="face ?? null" />
    </span>
    <div class="notice-row__body">
      <div v-if="!cont" class="notice-row__meta">
        <span class="notice-row__name" data-user-content>{{ name }}</span>
        <span class="notice-row__meta-time">{{ time }}</span>
      </div>
      <slot />
    </div>
  </div>
  <div v-else class="notice-row">
    <span class="notice-row__mark" :title="t('work.room.notice.platform')">
      <span class="notice-row__platform" aria-hidden="true">
        <v-icon size="12">mdi-server</v-icon>
      </span>
    </span>
    <div class="notice-row__body notice-row__body--platform">
      <slot />
    </div>
    <span class="notice-row__time">{{ time }}</span>
  </div>
</template>

<style scoped>
/* 平台自己的那一行：头像列 16px 起、28px 宽（20px 的记号在里面居中），正文从
   54px 起，和消息正文同一条竖线。 */
.notice-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 2px 16px 2px 20px;
  margin-top: 8px;
}
/* 连着几件事贴在一起，和同一个人连着说的几句话一样。 */
.notice-row + .notice-row {
  margin-top: 2px;
}
/* 队友的那一行：和消息行（room-row.css 的 .im-row）同一套几何。底色块（RoomNotice
   的 sys-row--warn/danger）往左伸出的量比平台行少一点：28px 的头像右边只留 10px，
   伸出 8px 就贴上了。 */
.notice-row--agent {
  padding: 4px 16px;
  --notice-wash-inset: 6px;
}
.notice-row--agent.notice-row--cont {
  margin-top: 0;
}
.notice-row__gutter {
  flex: 0 0 28px;
}
.notice-row__gutter-time {
  display: block;
  margin-left: -12px;
  width: 40px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-13);
  color: var(--faint);
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
@media (hover: hover) {
  .notice-row:hover .notice-row__gutter-time {
    opacity: 1;
  }
}
.notice-row__avatar,
.notice-row__mark {
  display: inline-flex;
  flex: none;
}
.notice-row__live {
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}
.notice-row__platform {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--muted);
}
.notice-row__body {
  flex: 1;
  min-width: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.notice-row__body--platform {
  padding-left: 4px;
}
/* 名字和时间：和消息行的 .im-meta / .im-name / .im-time 同一档。 */
.notice-row__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}
.notice-row__name {
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.notice-row__meta-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.notice-row__time {
  flex: none;
  color: var(--faint);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  padding-top: 1px; /* 18px 的行盒放进 20px 的记号高度里，上下各 1px */
}
</style>
