<script setup lang="ts">
// 设置: the one place. A table of contents on the left and the project's
// settings on the right, drawn with the product's settings rows.
import { S, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'

const SECTIONS = ['频道 · 实验记录', '项目', '芝士', '环境', '通知']
</script>

<template>
  <section class="ot-set" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">设置</h2>
      <span class="ot-head__sub">只有这一处</span>
    </header>
    <div class="ot-set__split">
      <nav class="ot-set__toc">
        <button v-for="(s, i) in SECTIONS" :key="s" type="button" class="ot-set__toc-row" :class="{ 'is-on': i === 1 }">
          {{ s }}
        </button>
      </nav>
      <div class="ot-set__pane">
        <h3 class="t-title ot-set__title">项目 · 多模态检索论文复现</h3>
        <SettingsRow
          label="芝士的改动"
          description="默认先待定，你一键接受；改成直接生效后随时可撤回"
          width="select-wide"
        >
          <SegmentedControl
            :model-value="S.settingsAgentDirect ? 'direct' : 'pending'"
            :options="[
              { value: 'pending', label: '先待定' },
              { value: 'direct', label: '直接生效' },
            ]"
            label="芝士的改动"
            @update:model-value="S.settingsAgentDirect = $event === 'direct'"
          />
        </SettingsRow>
        <SettingsRow label="代码受保护" description="打开后，人的代码改动也要别人采纳才生效" width="select">
          <v-switch v-model="S.settingsProtectCode" color="primary" density="compact" hide-details inset />
        </SettingsRow>
        <SettingsRow label="成员和 AI 队友" description="林晓、王磊、陈思远、赵雨桐、芝士" width="select">
          <BaseButton kind="secondary" size="sm" @click="toast('演示里不管理成员')">管理</BaseButton>
        </SettingsRow>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-set {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.ot-set__split {
  display: flex;
  flex: 1;
  min-height: 0;
}

.ot-set__toc {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  width: 200px;
  padding: 12px 8px;
  background: var(--canvas);
  border-right: 1px solid var(--line);
}

.ot-set__toc-row {
  height: 32px;
  padding: 0 12px;
  font-size: 14px;
  color: var(--text);
  text-align: left;
  border-radius: var(--radius-md);
}

.ot-set__toc-row:hover {
  background: var(--fill);
}

.ot-set__toc-row.is-on {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.ot-set__pane {
  flex: 1;
  min-width: 0;
  padding: 24px;
  overflow: auto;
}

.ot-set__title {
  margin: 0 0 8px;
  color: var(--ink);
}
</style>
