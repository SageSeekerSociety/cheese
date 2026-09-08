<script setup lang="ts">
// 内容在路上时，先画出它的形状。
//
// 判据（工作台的加载态都按这条挑）：**你知道等会儿长什么样，就画骨架；不知道要
// 等多久、也没有形状，就转圈。** 列表、消息流、文档——形状是已知的，画骨架，读的
// 人一眼知道这块地方会长出什么、有多少；而一个居中的转圈只说了「在等」，把整块
// 区域的高度也一并藏了，内容到达时页面会跳一下。
// 反过来，「上传这张图」「保存这篇文档」是一个**动作**在进行，没有形状可画，那里
// 转圈才是对的——别把这个组件用到那种地方去。
//
// 形状写在这一个文件里，而不是每个调用点各画各的：九处各自拼骨头，就是九次跑偏
// 的机会。variant 对应的是**我们自己界面里真实存在的行**，不是通用的
// article/card——骨架的全部价值在于它和真东西同一个形状，尺寸对不上就只是一块
// 会闪的灰色。
withDefaults(
  defineProps<{
    /**
     * 画哪一种行：
     * - `list`   侧栏那种紧凑列表行（话题列表）
     * - `chat`   聊天消息行（左边 28px 头像 + 名字 + 正文）
     * - `roster` 名册行（26px 头像 + 两行字 + 右边一个小标）
     * - `text`   一段文字（面板里的正文、详情）
     */
    variant?: 'list' | 'chat' | 'roster' | 'text'
    /** 画几行。默认值按各自最常见的一屏给，调用点通常不用传。 */
    rows?: number
  }>(),
  { variant: 'text', rows: 0 }
)

/** 每种形态默认画几行：够填满它所在的那块区域，又不至于假装内容比实际多。 */
const DEFAULT_ROWS: Record<string, number> = { list: 6, chat: 4, roster: 3, text: 3 }

/** 每一行的正文宽度都不一样，否则一排等长的灰条看着像表格而不像文字。 */
const WIDTHS = ['92%', '78%', '85%', '64%', '88%', '72%']
</script>

<template>
  <!-- aria-busy + 一句给读屏软件的话：骨架对眼睛说「在加载」，对屏幕阅读器
       什么都没说——它读到的只是一堆空 div。 -->
  <div class="skel" role="status" aria-busy="true" aria-live="polite">
    <span class="skel__sr">加载中</span>

    <template v-if="variant === 'chat'">
      <div v-for="i in rows || DEFAULT_ROWS.chat" :key="i" class="skel__chat">
        <div class="skel__bone skel__bone--avatar" />
        <div class="skel__chat-main">
          <div class="skel__bone skel__bone--name" />
          <div class="skel__bone skel__bone--line" :style="{ width: WIDTHS[i % WIDTHS.length] }" />
        </div>
      </div>
    </template>

    <template v-else-if="variant === 'roster'">
      <div v-for="i in rows || DEFAULT_ROWS.roster" :key="i" class="skel__roster">
        <div class="skel__bone skel__bone--face" />
        <div class="skel__roster-main">
          <div class="skel__bone skel__bone--name" />
          <div class="skel__bone skel__bone--handle" />
        </div>
        <div class="skel__bone skel__bone--tag" />
      </div>
    </template>

    <template v-else-if="variant === 'list'">
      <div v-for="i in rows || DEFAULT_ROWS.list" :key="i" class="skel__list">
        <div class="skel__bone skel__bone--line" :style="{ width: WIDTHS[i % WIDTHS.length] }" />
      </div>
    </template>

    <template v-else>
      <div v-for="i in rows || DEFAULT_ROWS.text" :key="i" class="skel__text">
        <div class="skel__bone skel__bone--line" :style="{ width: WIDTHS[i % WIDTHS.length] }" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.skel {
  width: 100%;
}
/* 只给读屏软件的一行字。visibility/display 都不行——那两个会让读屏软件也读不到。 */
.skel__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* 骨头本身。--fill-2 是「面上再进一层」的那一档，浅深两套主题里都是离
   --surface 一步、看得见但不抢眼的灰，所以这里不需要为深色再写一遍。 */
.skel__bone {
  position: relative;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}
/* 扫光。它是这个组件里唯一一处非用户触发的动效，理由是它就是那个「还在等」的
   信号本身——不动的灰条和「加载失败留下一片空白」看起来一模一样。 */
.skel__bone::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, var(--fill), transparent);
  animation: skel-sweep 1.4s ease-in-out infinite;
}
@keyframes skel-sweep {
  to {
    transform: translateX(100%);
  }
}
/* 全局那条 prefers-reduced-motion 兜底只是把时长压到 0.001ms，扫光会变成一个
   停在末帧的静止渐变。这里干脆整个关掉：aria-busy 已经把「在加载」这件事说清楚
   了，不需要靠动来说。 */
@media (prefers-reduced-motion: reduce) {
  .skel__bone::after {
    animation: none;
    background: none;
  }
}

.skel__bone--line {
  height: 12px;
}
.skel__bone--name {
  width: 84px;
  height: 12px;
  margin-bottom: 8px;
}
.skel__bone--handle {
  width: 56px;
  height: 10px;
  margin-top: 5px;
}
.skel__bone--tag {
  width: 40px;
  height: 14px;
  flex: none;
}
/* 头像的尺寸和圆角照抄真东西：ChatPanel 的 .im-avatar 是 28px / 8px，
   TopicMembers 的 .roster__avatar 是 26px / 8px。对不上的话内容到达那一刻
   整行会挪一下，而骨架存在的意义正是不让它挪。 */
.skel__bone--avatar {
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 8px;
}
.skel__bone--face {
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 8px;
}

/* 行的间距同样照抄真东西：.im-row 是 4px 16px + margin-top 8px，
   .roster__item 是 6px 14px。 */
.skel__chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 16px;
  margin-top: 8px;
}
.skel__chat-main {
  flex: 1 1 auto;
  min-width: 0;
}
.skel__roster {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 14px;
}
.skel__roster-main {
  flex: 1 1 auto;
  min-width: 0;
}
.skel__list {
  display: flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 16px;
}
.skel__text {
  padding: 6px 0;
}
</style>
