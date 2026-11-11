<script setup lang="ts">
// 第 3 周题单's document, pinned in 项目文件: the 题单's entry point. Its live
// block (the same pattern as a channel 文档's 这个频道的事) has one row per
// problem read from the 题目's submissions, so a submission a moment ago is
// already here: 通过 or not, how many tries, the latest score. A row opens that
// problem's main.cpp. Under the D 方案 its 「提交」 submits that one problem to the
// 题目 from here; under the others the document only reads, and each problem
// is submitted from the 题单's page in the space (TopicDetail). Under it, the
// teacher's few lines about the 题单.
import { computed } from 'vue'

import { SET, topicOf, WEEK3_INTRO } from './course'
import DemoDoc from './DemoDoc.vue'
import { latestOf, openFile, openSubmit, passed as ok, resultOf, S } from './state'
import { onCard } from './variants'

import BaseButton from '@/components/base/BaseButton.vue'
import { monthDayTime } from '@/lib/dateText'

const rows = computed(() =>
  SET.map((p) => {
    const last = latestOf('week3', p.id)
    return {
      ...p,
      tries: (S.subs.week3 ?? []).filter((x) => x.unit === p.id).length,
      last,
      result: last ? resultOf(last).text : '—',
      passed: ok(last),
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
        <div class="ot-pset__cols t-eyebrow-read" :class="{ 'ot-pset__cols--bare': !onCard() }">
          <span>题目</span><span>状态</span><span class="ot-pset__tries">尝试</span><span>最近得分</span>
        </div>
        <div v-for="r in rows" :key="r.id" class="ot-pset__row" :data-set="r.id">
          <button type="button" class="ot-cdoc__row" @click="openFile(`${r.id}/main.cpp`)">
            <span class="ot-pset__name"
              ><code>{{ r.id }}</code> {{ r.title }}</span
            >
            <span class="ot-pset__st" :class="{ 'is-ok': r.passed, 'is-bad': r.last?.verdict && !r.passed }">
              <v-icon v-if="r.passed" size="14" icon="mdi-check" />{{
                r.passed ? '通过' : r.last?.verdict ? '没通过' : r.last ? '评测中' : '未提交'
              }}
            </span>
            <span class="ot-pset__n ot-pset__tries">{{ r.tries ? `${r.tries} 次` : '—' }}</span>
            <span class="ot-pset__n">{{ r.result }}</span>
          </button>
          <BaseButton
            v-if="onCard()"
            kind="secondary"
            size="sm"
            class="ot-pset__submit"
            @click="openSubmit('week3', r.id)"
            >提交</BaseButton
          >
        </div>
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
  container-type: inline-size;
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
  /* Clear of each row's 提交 (56px), the gap before it and the row's own right padding. */
  padding-right: 72px;
  margin: 8px 0 2px;
}

/* No 提交 beside the rows: the columns line up with the row's own padding. */
.ot-pset__cols--bare {
  padding-right: 8px;
}

/* The row opens the problem's file; its 提交 sits beside it, outside the row's button. */
.ot-pset__row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ot-pset__row .ot-cdoc__row {
  flex: 1;
  min-width: 0;
  margin-right: 0;
}

.ot-pset__submit {
  flex: none;
  width: 56px;
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
  /* Narrow enough that the problem's name keeps its room beside each row's 提交. */
  width: 52px;
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

/* Beside a wide right panel the column is narrow: the problem's name keeps its room, 尝试 gives way. */
@container (max-width: 440px) {
  .ot-pset__tries {
    display: none;
  }
}

.ot-cdoc__people {
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}
</style>
