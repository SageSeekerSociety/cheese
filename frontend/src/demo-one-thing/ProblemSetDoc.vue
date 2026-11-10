<script setup lang="ts">
// 第 3 周题单's document, pinned in 项目文件: the 题单's entry point. Its live
// block (the same pattern as a channel 文档's 这个频道的事) has one row per
// problem read from the project's 评测记录, so a submission a moment ago is
// already here: 通过 or not, how many tries, the latest score. A row opens that
// problem's main.cpp. Under it, the teacher's few lines about the 题单.
import { computed } from 'vue'

import { SET, topicOf, WEEK3_INTRO } from './course'
import DemoDoc from './DemoDoc.vue'
import { latestOf, openFile, S } from './state'

import { monthDayTime } from '@/lib/dateText'

const rows = computed(() =>
  SET.map((p) => {
    const last = latestOf('week3', p.id)
    return {
      ...p,
      tries: S.subs.week3.filter((x) => x.unit === p.id).length,
      last,
      passed: !!last && last.score === last.total,
    }
  })
)
const passed = computed(() => rows.value.filter((r) => r.passed).length)
const due = monthDayTime(Date.parse(topicOf('week3').due))
</script>

<template>
  <div class="ot-cdoc" data-zone="doc">
    <div class="ot-cdoc__page">
      <header class="ot-cdoc__head">
        <h1 class="ot-cdoc__title">第 3 周题单</h1>
        <span class="ot-cdoc__edited">张老师发布 · 截止 {{ due }}</span>
      </header>

      <section class="ot-cdoc__block" data-zone="set-list">
        <div class="ot-cdoc__blockhead">
          <h2 class="ot-cdoc__h">这个题单</h2>
          <span class="ot-cdoc__note">实时 · 通过 {{ passed }}/{{ rows.length }}</span>
        </div>
        <div class="ot-pset__cols t-eyebrow-read">
          <span>题目</span><span>状态</span><span>尝试</span><span>最近得分</span>
        </div>
        <button
          v-for="r in rows"
          :key="r.id"
          type="button"
          class="ot-cdoc__row ot-pset__row"
          :data-set="r.id"
          @click="openFile(`${r.id}/main.cpp`)"
        >
          <span class="ot-pset__name"
            ><code>{{ r.id }}</code> {{ r.title }}</span
          >
          <span class="ot-pset__st" :class="{ 'is-ok': r.passed, 'is-bad': r.last && !r.passed }">
            <v-icon v-if="r.passed" size="14" icon="mdi-check" />{{ r.passed ? '通过' : r.last ? '没通过' : '未提交' }}
          </span>
          <span class="ot-pset__n">{{ r.tries ? `${r.tries} 次` : '—' }}</span>
          <span class="ot-pset__n">{{ r.last ? `${r.last.score}/${r.last.total}` : '—' }}</span>
        </button>
      </section>

      <section class="ot-cdoc__people">
        <DemoDoc key="week3-intro" :html="WEEK3_INTRO" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.ot-cdoc {
  height: 100%;
  overflow: auto;
}

.ot-cdoc__page {
  max-width: 720px;
  padding: 20px 32px 48px;
}

.ot-cdoc__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}

.ot-cdoc__title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--ink);
}

.ot-cdoc__edited,
.ot-cdoc__note {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-cdoc__block {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.ot-cdoc__blockhead {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.ot-cdoc__h {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.ot-cdoc__row {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  margin: 0 -8px;
  font-size: 14px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  box-sizing: content-box;
}

.ot-cdoc__row:hover {
  background: var(--fill);
}

.ot-pset__cols {
  display: flex;
  gap: 10px;
  margin: 8px 0 2px;
}

.ot-pset__cols > :first-child,
.ot-pset__name {
  flex: 1;
  min-width: 0;
}

.ot-pset__cols > :not(:first-child),
.ot-pset__st,
.ot-pset__n {
  flex: none;
  width: 72px;
  text-align: right;
}

.ot-pset__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-pset__name code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}

.ot-pset__st {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  justify-content: flex-end;
  font-size: 13px;
  color: var(--muted);
}

.ot-pset__st.is-ok {
  color: var(--ok-ink);
}

.ot-pset__st.is-bad {
  color: var(--text);
}

.ot-pset__n {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}

.ot-cdoc__people {
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}
</style>
