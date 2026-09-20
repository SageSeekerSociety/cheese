<script setup lang="ts">
import { computed, ref } from 'vue'

import ArchDiagram from '@/components/diagram/ArchDiagram.vue'
import ErDiagram from '@/components/diagram/ErDiagram.vue'
import { feedbackArch, feedbackEr } from '@/lib/feedbackDiagram'
import { STATUS_LADDER, STATUS_META } from '@/lib/feedbackMeta'

// /design/feedback —— 数据关系与架构关系。
//
// 这一页不是给人「用」的，是给人**核对**的：表开得对不对、agent 那条路绕不绕、
// 状态机是不是五档。所以它只画关系，不带任何操作 —— 要操作请回反馈中心。
//
// 图数据在 lib/feedbackDiagram.ts；表和状态机的那部分**已经实现**了，落在
// backend/app/domain/feedback/（models / repositories / services / proposals），
// 图和代码不一致时以代码为准，方案的来龙去脉在
// docs/topics/反馈功能后端设计-方案稿.md。
defineOptions({ name: 'FeedbackDesignPage' })

const tab = ref<'er' | 'flow'>('er')

const TABS = [
  { value: 'er', label: '表与关系' },
  { value: 'flow', label: '数据流' },
] as const

/**
 * 五档状态。**不在这里另抄一份标签** —— `STATUS_META` 和 `STATUS_LADDER` 是前端
 * 唯一一份（`lib/feedbackMeta.ts`），页面和组件都从那里取。这里再写一遍，加状态时
 * 就会多出一个没人会想起来的副本。
 *
 * 注意梯子的**真值在服务端**（`GET /feedback/meta` 的 `status_ladder`），这一份是
 * meta 还没到的那一帧的兜底，也是这一页要对照的那份「设计意图」。
 */
const ladder = computed(() =>
  STATUS_LADDER.map((value) => ({ value, label: STATUS_META[value].label, dot: STATUS_META[value].dot }))
)

/**
 * 方案稿 §8 里曾经卡着开工的三条。它们**已经有实现口径了**（每条都是常量或一处
 * 判断，改起来是一行），所以这里如实写「定成了什么、在哪」，而不是继续挂着问号 ——
 * 一页核对用的图上面如果还写着「未定」，读的人不会去代码里找答案，他会以为功能没做。
 */
const DECIDED = [
  {
    no: '§8.1',
    what: '平台管理员是谁',
    why: 'settings.feedback_admin_handles（配置项，默认空 = 没人）。空的时候管理端谁都进不去，这是安全的一侧。',
  },
  {
    no: '§8.3',
    what: 'security 与 visibility 的关系',
    why: 'security 是 private 之下的一层收窄，不是第二个开关：读的时候 visibility 先判、security 再收窄一次。',
  },
  {
    no: '§8.23',
    what: '默认筛选的口径',
    why: '默认列表只沉底**已解决的 bug**；建议和其他的已解决项仍然留在列表里。',
  },
] as const
</script>

<template>
  <!-- 滚动归这一页自己领，理由见 FeedbackCenterPage 顶部那段注释。 -->
  <div class="fd-page fill-height overflow-y-auto">
    <div class="fd-page__inner page-container page-container--wide">
      <header class="fd-head">
        <h1 class="t-page-title">数据与架构</h1>
        <v-spacer />
        <v-btn variant="text" color="secondary" size="small" to="/feedback">回反馈中心</v-btn>
      </header>

      <p class="fd-lede">
        这套东西长什么样：表怎么开、谁在写、状态怎么走。图上的每一处都能在
        <code>docs/topics/反馈功能后端设计-方案稿.md</code> 里找到出处。
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

      <section class="fd-open">
        <h3 class="fd-open__title">曾经卡着开工的三条，现在都有答案了</h3>
        <p class="fd-open__sub">
          这三条当初是「不定就没法写」的问题，方案稿里给的是问句。现在它们是实现里的
          <strong>三个常量</strong>（一条配置项、一处读时收窄、一句筛选口径）—— 改起来是一行，
          所以这里写的是定成了什么，而不是继续挂着问号。
        </p>
        <ul class="fd-open__list">
          <li v-for="item in DECIDED" :key="item.no" class="fd-open__item">
            <span class="fd-open__no">{{ item.no }}</span>
            <span class="fd-open__what">{{ item.what }}</span>
            <span class="fd-open__why">{{ item.why }}</span>
          </li>
        </ul>
      </section>

      <p class="fd-foot">
        图上的表和状态机已经实现（<code>backend/app/domain/feedback/</code>），接口、迁移和取舍写在方案稿里；
        两边不一致时以代码为准。
      </p>
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

.fd-open {
  padding-top: 22px;
  border-top: 1px solid var(--line);
}

.fd-open__title {
  margin-bottom: 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.fd-open__sub {
  max-width: 720px;
  margin-bottom: 14px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.fd-open__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.fd-open__item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 9px 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.fd-open__no {
  width: 44px;
  flex: 0 0 auto;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--faint);
}

.fd-open__what {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.fd-open__why {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--muted);
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
