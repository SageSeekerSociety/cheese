<script setup lang="ts">
// A 题目 in the course, center column: my own copy of the code, edited
// directly. There is no 采纳 and no 正式版 here (the course is set to
// 「代码：每人一份，直接改」): 运行样例 runs the statement's samples, 提交评测
// sends the copy as it is to the judge. A gdb 题目 has no code to write here.
import { computed, ref, watch } from 'vue'

import { problemById, standing } from './course'
import DemoCodeArea from './DemoCodeArea.vue'
import JudgeResult from './JudgeResult.vue'
import { runProblemSamples, S, submitProblem, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { monthDayTime } from '@/lib/dateText'

const p = computed(() => problemById(S.problem)!)
const subs = computed(() => S.subs[p.value.id] ?? [])
const code = computed({
  get: () => S.code[p.value.id] ?? p.value.starter,
  set: (v: string) => (S.code[p.value.id] = v),
})
const state = computed(() => standing(subs.value))
const stateText = computed(() =>
  state.value === 'passed' ? '已通过' : state.value === 'trying' ? `尝试中 · ${subs.value.length} 次` : '未开始'
)

/** What the pane under the editor shows: the samples' output, or the judge's verdict. */
const pane = ref<'sample' | 'judge' | null>(null)
watch(
  () => S.problem,
  () => (pane.value = null)
)
const judging = computed(() => S.judging === p.value.id)
function run() {
  pane.value = 'sample'
  runProblemSamples()
}
function submit() {
  pane.value = 'judge'
  submitProblem()
}
</script>

<template>
  <section class="ot-prob" data-zone="center">
    <header class="ot-head">
      <v-icon size="18" class="ot-prob__icon" :icon="p.kind === 'gdb' ? 'mdi-bomb' : 'mdi-code-braces'" />
      <h2 class="ot-head__title">{{ p.title }}</h2>
      <span class="ot-prob__state" :class="`is-${state}`">
        <v-icon v-if="state === 'passed'" size="14" icon="mdi-check" />{{ stateText }}
      </span>
      <span class="ot-head__gap" />
      <span class="ot-head__sub">截止 {{ monthDayTime(Date.parse(p.due)) }}</span>
    </header>

    <template v-if="p.kind === 'code'">
      <div class="ot-prob__bar">
        <v-icon size="15" icon="mdi-file-code-outline" class="ot-prob__mark" />
        <span class="ot-prob__file">{{ p.file }}</span>
        <span class="ot-prob__copy">我的代码 · 只有我和老师看得到</span>
        <span class="ot-head__gap" />
        <BaseButton
          id="b-run-samples"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-play"
          :loading="S.sampling"
          @click="run"
          >运行样例</BaseButton
        >
        <BaseButton id="b-submit" kind="primary" size="sm" :loading="judging" @click="submit">提交评测</BaseButton>
      </div>
      <DemoCodeArea v-model="code" :label="`编辑 ${p.file}`" />
      <div v-if="pane" class="ot-prob__out" role="status">
        <template v-if="pane === 'sample'">
          <pre v-if="S.sampleOut[p.id]" class="ot-prob__res">{{ S.sampleOut[p.id] }}</pre>
          <div v-else class="ot-prob__wait">运行样例…</div>
        </template>
        <template v-else>
          <div v-if="judging" class="ot-prob__wait">评测中… 编译、跑 dlc、跑测试</div>
          <JudgeResult v-else-if="subs[0]" :sub="subs[0]" />
        </template>
      </div>
    </template>

    <div v-else class="ot-prob__gdb">
      <BaseEmptyState
        size="page"
        icon="mdi-console"
        title="这道题不在这里写代码"
        desc="在课程服务器上你自己的终端里用 gdb 拆炸弹，答案按行写进 answers.txt，用 ./bomb answers.txt 自己验证。拆掉之后服务器自动记分。"
        action="打开终端"
        @action="toast('演示里没有终端：课程服务器上每人一台')"
      />
    </div>
  </section>
</template>

<style scoped>
.ot-prob {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-prob__icon {
  color: var(--muted);
}

.ot-prob__state {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 0 8px;
  font-size: 12px;
  line-height: 20px;
  color: var(--muted);
  white-space: nowrap;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-prob__state.is-passed {
  color: var(--ok-ink);
  background: var(--ok-wash);
}

.ot-prob__bar {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 40px;
  padding: 0 8px 0 16px;
  font-size: 13px;
  background: var(--fill);
  border-bottom: 1px solid var(--line);
}

.ot-prob__bar > * {
  flex: none;
}

.ot-prob__mark {
  color: var(--muted);
}

.ot-prob__file {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

.ot-prob__copy {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-prob__bar > .ot-head__gap {
  flex: 1;
}

.ot-prob__out {
  flex: none;
  max-height: 220px;
  padding: 8px 16px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.55;
  background: var(--fill);
  border-top: 1px solid var(--line);
}

.ot-prob__res {
  margin: 0;
  font-family: var(--font-mono);
  color: var(--ink);
  white-space: pre-wrap;
}

.ot-prob__wait {
  font-family: var(--font-mono);
  color: var(--muted);
}

.ot-prob__gdb {
  flex: 1;
  min-height: 0;
  padding: 48px 32px;
}
</style>
