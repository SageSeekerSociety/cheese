<script setup lang="ts">
// 动效台的一格小样：八种变化各一件，画在同一个盒子里。
//
// 这里画的都是**示意件**，不是产品里的组件：产品那几个（工作面板、页签、发送键）
// 各自要一整套 props 和上下文，摆八份进来会把这一页变成又一份目录。要守的只有一件
// 事——这一格里每一条 transition 都引产品的 token，一个毫秒数、一条贝塞尔、一个
// 色值都不写死。画在哪个档、动多少，全由 `DemoMotionPage` 通过容器的 `--dur-*` 决定。
//
// 每一格的「重放」是把它整件重新挂一遍（`DemoMotionPage` 换的 `key`），动效就从初始态
// 再走一次；不动它的时候盒子停在终态，读的人看到的是这个东西长什么样。这一件身上没有
// 「重放」这个属性：一个组件换不掉自己，那件事得由挂它的那一层做。
import type { TransitionId } from './demoTransitions'

import { onMounted, ref } from 'vue'

defineProps<{ id: TransitionId }>()

/** 挂上以后下一帧才翻到终态：同一帧里画出来又改掉，浏览器不会播过渡。 */
const on = ref(false)
onMounted(() => requestAnimationFrame(() => (on.value = true)))
</script>

<template>
  <div class="sample" :data-on="on ? 'on' : 'off'" :data-id="id">
    <!-- Card expanding: height and opacity move together. -->
    <div v-if="id === 'expand'" class="card">
      <p class="card-head">这一格在讲什么</p>
      <p class="card-tail">点一下，内容从收着到展开。</p>
      <div class="card-body">
        <p class="card-line">展开后是这几句说明，高度撑到位就停。</p>
      </div>
    </div>

    <!-- Overlay appearing: the scrim fades in first, the dialog grows from 92% to 100%. -->
    <div v-else-if="id === 'dialog'" class="scrim">
      <div class="dialog">
        <p class="dialog-head">归档这个频道？</p>
        <p class="dialog-body">归档后任务照常，只是不再出现在列表里。</p>
      </div>
    </div>

    <!-- Panel: slides in from the right. -->
    <div v-else-if="id === 'drawer'" class="drawer-well">
      <div class="drawer">工作面板</div>
    </div>

    <!-- Tab indicator: slides along the same track, not fade out then in. -->
    <div v-else-if="id === 'tabs'" class="tabs">
      <span class="tabs-pill" />
      <span class="tabs-tab">对话</span>
      <span class="tabs-tab">改动</span>
      <span class="tabs-tab">文档</span>
    </div>

    <!-- New message: fades in from 8px below and stops where it lands. -->
    <p v-else-if="id === 'row'" class="row">芝士人：找到了，页面刚加载时浏览器自己滚了一下。</p>

    <!-- Press feedback: sinks on the press itself, not on release. -->
    <div v-else-if="id === 'ripple'" class="press">采纳</div>

    <!-- Phone bottom sheet: rises from the bottom and sits flush against it. -->
    <div v-else-if="id === 'sheet'" class="sheet-well">
      <div class="sheet">在浏览器打开</div>
    </div>

    <!-- Skeleton to content: grey bars first, swapped in place once the content arrives. -->
    <div v-else class="skeleton">
      <span class="skeleton-line" />
      <span class="skeleton-line" />
      <span class="skeleton-line" />
    </div>
  </div>
</template>

<style scoped>
/* ---- 盒子 ---- */
.sample {
  display: grid;
  position: relative;
  width: 100%;
  height: 172px;
  overflow: hidden;
  background: var(--fill);
  place-items: center;
}

/* ---- 小样们共用的底 ---- */
.card,
.dialog,
.row,
.press {
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

/* ---- 卡片展开 ---- */
.card {
  width: 190px;
  padding: 10px 12px;
}

.card-head {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.card-tail,
.card-line {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.card-body {
  max-height: 0;
  overflow: hidden;
  opacity: 0;
  transition:
    max-height var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}

.sample[data-on='on'] .card-body {
  max-height: 90px;
  opacity: 1;
}

/* ---- 浮层出现 ---- */
.scrim {
  display: grid;
  position: absolute;
  inset: 0;
  background: var(--overlay);
  place-items: center;
  /* 遮罩也一起淡进来。不淡的话初始态是一块暗底托着个看不见的对话框，看着像坏了。 */
  opacity: 0;
  transition: opacity var(--dur-base) var(--ease-out);
}

.sample[data-on='on'] .scrim {
  opacity: 1;
}

.dialog {
  width: 200px;
  padding: 14px;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  opacity: 0;
  transform: scale(0.92);
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}

.sample[data-on='on'] .dialog {
  opacity: 1;
  transform: none;
}

.dialog-head {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.dialog-body {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

/* ---- 面板收放 ---- */
.drawer-well {
  display: flex;
  justify-content: flex-end;
  width: 230px;
  height: 120px;
  overflow: hidden;
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.drawer {
  display: grid;
  width: 132px;
  background: var(--surface);
  border-left: 1px solid var(--line-2);
  font-size: 12px;
  color: var(--muted);
  transform: translateX(132px);
  place-items: center;
  transition: transform var(--dur-slow) var(--ease-out);
}

.sample[data-on='on'] .drawer {
  transform: none;
}

/* ---- 页签指示条 ---- */
.tabs {
  display: flex;
  position: relative;
  padding: 3px;
  background: var(--fill-2);
  border-radius: var(--radius-md);
}

.tabs-pill {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 3px;
  width: 56px;
  background: var(--surface);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-1);
  transition: transform var(--dur-base) var(--ease-standard);
}

.sample[data-on='on'] .tabs-pill {
  transform: translateX(56px);
}

.tabs-tab {
  position: relative;
  z-index: 1;
  padding: 5px 0;
  font-size: 12px;
  color: var(--muted);
  text-align: center;
  width: 56px;
}

/* ---- 新消息进入 ---- */
.row {
  width: 200px;
  padding: 8px 10px;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  border-radius: var(--radius-md);
  opacity: 0;
  transform: translateY(8px);
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}

.sample[data-on='on'] .row {
  opacity: 1;
  transform: none;
}

/* ---- 按下回执 ---- */
.press {
  display: grid;
  width: 76px;
  height: 36px;
  font-size: 12px;
  color: var(--muted);
  background: var(--fill-2);
  border: 0;
  border-radius: var(--radius-md);
  place-items: center;
  transform: scale(0.92);
  transition: transform var(--dur-press) var(--ease-standard);
}

.sample[data-on='on'] .press {
  transform: none;
}

/* ---- 手机底部弹层 ---- */
.sheet-well {
  position: relative;
  width: 168px;
  height: 130px;
  overflow: hidden;
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.sheet {
  display: grid;
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 76px;
  background: var(--surface);
  border-top: 1px solid var(--line-2);

  /* 上面两个角由外框的圆角裁出来（`.sheet-well` 有 overflow: hidden），这里不再写一遍：
     自己写就得写成 `var(--radius-lg) var(--radius-lg) 0 0` 那种四个值的形状，门禁只认
     单值的那几档。 */
  font-size: 12px;
  color: var(--muted);
  transform: translateY(76px);
  place-items: center;
  transition: transform var(--dur-slow) var(--ease-out);
}

.sample[data-on='on'] .sheet {
  transform: none;
}

/* ---- 骨架到内容 ----
   这条动效是循环的，`--dur-slow` 决定扫一遍有多久。全局那条 reduced-motion 兜底会
   把时长压成 0.001ms，一个循环被压成那样就是频闪，比不动更糟；这里自己给它 `none`
   （产品的 `.v-progress-linear` 就是这么办的，`style.css` 里那一条）。 */
.skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 190px;
}

.skeleton-line {
  height: 10px;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.skeleton-line:nth-child(1) {
  width: 80%;
}

.skeleton-line:nth-child(2) {
  width: 60%;
}

.skeleton-line:nth-child(3) {
  width: 70%;
}

.sample[data-on='on'] .skeleton-line {
  background: linear-gradient(90deg, var(--fill-2) 0%, var(--fill) 50%, var(--fill-2) 100%);
  background-size: 200% 100%;
  animation: sample-shimmer var(--dur-slow) linear infinite;
}

@keyframes sample-shimmer {
  from {
    background-position: 200% 0;
  }

  to {
    background-position: -200% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sample[data-on='on'] .skeleton-line {
    background: var(--fill-2);
    animation: none;
  }
}
</style>
