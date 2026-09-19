<script setup lang="ts">
import { computed, ref } from 'vue'

import ArchDiagram from '@/components/diagram/ArchDiagram.vue'
import ErDiagram from '@/components/diagram/ErDiagram.vue'
import { feedbackArch, feedbackEr } from '@/lib/feedbackDiagram'
import { STATUS_LADDER, STATUS_META } from '@/lib/feedbackMock'

// /design/feedback —— 数据关系与架构关系。
//
// 这一页不是给人「用」的，是给人**核对**的：表开得对不对、agent 那条路绕不绕、
// 状态机是不是五档。所以它只画关系，不带任何操作 —— 要操作请回反馈中心。
//
// 图数据在 lib/feedbackDiagram.ts，内容抄自 docs/topics/反馈功能后端设计-方案稿.md。
defineOptions({ name: 'FeedbackDesignPage' })

const tab = ref<'er' | 'flow'>('er')

const TABS = [
  { value: 'er', label: '表与关系' },
  { value: 'flow', label: '数据流' },
] as const

/**
 * 五档状态。**不在这里另抄一份标签**——`STATUS_META` 和 `STATUS_LADDER` 是前端
 * 唯一一份，页面和组件都从那里取。这里再写一遍，加状态时就会多出一个没人会想起来的
 * 副本（`feedbackMock.ts` 里那条注释抱怨的正是这件事）。
 */
const ladder = computed(() =>
  STATUS_LADDER.map((value) => ({ value, label: STATUS_META[value].label, dot: STATUS_META[value].dot }))
)
</script>

<template>
  <div class="fd-page">
    <div class="fd-page__inner page-container page-container--wide">
      <header class="fd-head">
        <h1 class="t-page-title">数据与架构</h1>
        <v-spacer />
        <v-btn variant="text" color="secondary" size="small" to="/feedback">回反馈中心</v-btn>
      </header>

      <p class="fd-lede">
        原型背后那套东西长什么样：表怎么开、谁在写、状态怎么走。图上的每一处都能在
        <code>docs/topics/反馈功能后端设计-方案稿.md</code> 里找到出处，两边不一致时以方案稿为准。
      </p>

      <v-tabs v-model="tab" density="comfortable" color="primary" class="fd-tabs">
        <v-tab v-for="item in TABS" :key="item.value" :value="item.value">{{ item.label }}</v-tab>
      </v-tabs>

      <div v-if="tab === 'er'" class="fd-panel">
        <ErDiagram
          :entities="feedbackEr.entities"
          :relations="feedbackEr.relations"
          :title="feedbackEr.title"
          :subtitle="feedbackEr.subtitle"
        />

        <section class="fd-ladder">
          <h3 class="fd-ladder__title">状态只有五档，是一条梯子</h3>
          <p class="fd-ladder__sub">
            改状态和写时间线必须在同一个事务里发生 —— 只改其一，详情页右侧的时间线就会和卡片上的状态词对不上。
          </p>
          <ol class="fd-ladder__row">
            <li v-for="(step, index) in ladder" :key="step.value" class="fd-step">
              <span class="fd-step__dot" :style="{ background: step.dot }" />
              <span class="fd-step__label">{{ step.label }}</span>
              <span class="fd-step__raw">{{ step.value }}</span>
              <v-icon v-if="index < ladder.length - 1" size="14" class="fd-step__arrow">mdi-chevron-right</v-icon>
            </li>
          </ol>
        </section>
      </div>

      <div v-else class="fd-panel">
        <ArchDiagram :diagram="feedbackArch" />
      </div>

      <p class="fd-foot">这一页是原型的一部分，画的是**提案**而不是已实现的系统；接口、迁移和取舍写在方案稿里。</p>
    </div>
  </div>
</template>

<style scoped>
.fd-page {
  min-height: 100%;
  background: var(--canvas);
}

.fd-page__inner {
  padding-top: 28px;
  padding-bottom: 64px;
}

.fd-head {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
}

.fd-lede {
  max-width: 720px;
  margin-bottom: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--muted);
}

.fd-lede code {
  padding: 1px 5px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--text);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.fd-tabs {
  margin-bottom: 24px;
  border-bottom: 1px solid var(--line);
}

.fd-panel {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.fd-ladder {
  padding-top: 22px;
  border-top: 1px solid var(--line);
}

.fd-ladder__title {
  margin-bottom: 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.fd-ladder__sub {
  max-width: 720px;
  margin-bottom: 14px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.fd-ladder__row {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 0;
  margin: 0;
  list-style: none;
}

.fd-step {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
}

.fd-step__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-pill);
}

.fd-step__label {
  font-size: 13px;
  color: var(--text);
}

.fd-step__raw {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--faint);
}

.fd-step__arrow {
  margin-left: 3px;
  color: var(--faint);
}

.fd-foot {
  padding-top: 18px;
  margin-top: 32px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--faint);
  border-top: 1px solid var(--line);
}
</style>
