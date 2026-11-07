<script setup lang="ts">
import { ref } from 'vue'

import PanelSite from '../src/components/panels/PanelSite.vue'
import { usePanelSite } from '../src/composables/usePanelSite'

import { state, TASKS, writeNewRow } from './fakeServer'

const topic = ref<'task-a' | 'task-b'>('task-a')
const site = usePanelSite({
  topicId: () => topic.value,
  active: () => true,
  refreshTick: () => 0,
  runningTurns: () => ({}),
  agentControl: () => null,
})
const modes = [
  { v: 'ok', t: '正常' },
  { v: 'fail', t: '读失败（断网）' },
  { v: 'forbidden', t: '403 无权限' },
] as const
</script>

<template>
  <div class="sb">
    <header class="bar">
      <strong>现场缓存验收台</strong>
      <span class="tag">模拟：数据和接口都是假的，只有现场面板是 PR #5200 的真代码</span>
    </header>
    <div class="ctrl">
      <div>
        <span class="lbl">打开哪个任务</span>
        <button v-for="(name, id) in TASKS" :key="id" :class="{ on: topic === id }" @click="topic = id">{{ name }}</button>
      </div>
      <div>
        <span class="lbl">服务端此刻</span>
        <button v-for="m in modes" :key="m.v" :class="{ on: state.mode === m.v }" @click="state.mode = m.v">{{ m.t }}</button>
      </div>
      <div>
        <span class="lbl">服务端写数据</span>
        <button @click="writeNewRow(topic)">给当前任务写一条新记录</button>
      </div>
    </div>
    <div class="body">
      <main class="pane">
        <PanelSite :topic-id="topic" :active="true" :member-names="{ 'cheese-demo': '芝士Opus（假）' }" :site="site" />
      </main>
      <aside class="log">
        <div class="lbl">最近几次读取（每 3 秒自动刷新一次）</div>
        <div v-for="(l, i) in state.log" :key="i" class="line">{{ l }}</div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.sb { display: flex; flex-direction: column; height: 100vh; font-size: 14px; }
.bar { padding: 10px 16px; border-bottom: 1px solid #e5e7eb; display: flex; gap: 12px; align-items: center; }
.tag { color: #b45309; background: #fef3c7; border-radius: 6px; padding: 2px 8px; font-size: 12px; }
.ctrl { display: flex; flex-wrap: wrap; gap: 18px; padding: 10px 16px; border-bottom: 1px solid #e5e7eb; background: #fafafa; }
.lbl { color: #6b7280; margin-right: 8px; font-size: 12px; }
button { border: 1px solid #d1d5db; border-radius: 6px; padding: 4px 10px; margin-right: 6px; background: #fff; cursor: pointer; }
button.on { background: #111827; color: #fff; border-color: #111827; }
.body { flex: 1; display: flex; min-height: 0; }
.pane { flex: 1; min-width: 0; border-right: 1px solid #e5e7eb; overflow: auto; display: flex; flex-direction: column; }
.log { width: 300px; padding: 10px 12px; overflow: auto; font-size: 12px; }
.line { padding: 3px 0; border-bottom: 1px dashed #eee; color: #374151; }
</style>
