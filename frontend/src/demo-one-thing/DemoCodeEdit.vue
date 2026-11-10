<script setup lang="ts">
// 改代码: a person edits a code file directly, on their own copy of the official
// version, runs it, and merges it. A person's edit takes effect when merged
// (芝士's wait for a person's 采纳); with 代码受保护 on, it waits for someone
// else's 采纳 too. No 一件事 is opened for it. The editing surface is
// DemoCodeArea (why it is not Monaco is said there).
import { computed, ref } from 'vue'

import DemoCodeArea from './DemoCodeArea.vue'
import { changedLines, mergeCode, S } from './state'

import BaseButton from '@/components/base/BaseButton.vue'

const draft = computed(() => S.codeDraft!)
const lines = computed(() => draft.value.text.split('\n'))
const changed = computed(() => changedLines(draft.value.path, lines.value))

// ---- 运行: a short, plausible run of the file as it now reads ----
const running = ref(false)
const output = ref<{ cmd: string; out: string } | null>(null)

function scripted(path: string, text: string): { cmd: string; out: string } {
  if (path === 'eval/preprocess.py') {
    const crop = /CenterCrop\(\s*224\s*\)/.test(text)
    const size = /Resize\(\s*(\d+)/.exec(text)?.[1] ?? '256'
    const shape = crop ? '[3, 224, 224]' : `[3, ${size}, ${Math.round((Number(size) * 4) / 3)}]`
    return {
      cmd: 'python -c "from eval.preprocess import preprocess; ..." data/coco/val2017/000000000139.jpg',
      out: `torch.Size(${shape})  dtype=torch.float32\n${crop ? '和模型输入 224×224 一致' : '注意：不是 224×224，模型会在里面再缩放一次'}`,
    }
  }
  if (path === 'eval/retrieval.py') {
    const all = text.includes('recall_at_k')
    return {
      cmd: 'python -m eval.retrieval --data flickr30k --split 1k --limit 100',
      out: `encoding 100 images, 500 captions … done (3.8s)\n${all ? 'R@1 79.0  R@5 94.0  R@10 97.0' : 'R@1 78.0'}   （只抽了前 100 张，快速检查）`,
    }
  }
  if (path.endsWith('.yaml'))
    return {
      cmd: `python -c "import yaml; print(list(yaml.safe_load(open('${path}'))))"`,
      out: "['model', 'data', 'eval']",
    }
  return { cmd: `bash -n ${path}`, out: '语法检查通过（只检查，不下载）' }
}

function run() {
  running.value = true
  output.value = null
  const { path, text } = draft.value
  setTimeout(() => {
    running.value = false
    output.value = scripted(path, text)
  }, 900)
}

function cancel() {
  S.codeDraft = null
}
</script>

<template>
  <div class="ot-ced">
    <div class="ot-ced__bar">
      <v-icon size="15" icon="mdi-source-branch" class="ot-ced__mark" />
      <span class="ot-ced__copy">你的副本 · 基于正式版</span>
      <span class="ot-head__gap" />
      <BaseButton kind="ghost" size="sm" @click="cancel">放弃</BaseButton>
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-play" :loading="running" @click="run">运行</BaseButton>
      <BaseButton id="b-merge-code" kind="primary" size="sm" :disabled="!changed" @click="mergeCode">
        {{ S.settingsProtectCode ? '请人采纳' : '合进正式版' }}
      </BaseButton>
    </div>
    <DemoCodeArea v-model="S.codeDraft!.text" :label="`编辑 ${draft.path}`" />
    <div v-if="running || output" class="ot-ced__out" role="status">
      <template v-if="output">
        <div class="ot-ced__cmd">$ {{ output.cmd }}</div>
        <pre class="ot-ced__res">{{ output.out }}</pre>
      </template>
      <div v-else class="ot-ced__cmd">运行中…</div>
    </div>
  </div>
</template>

<style scoped>
.ot-ced {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.ot-ced__bar {
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

.ot-ced__mark {
  color: var(--muted);
}

.ot-ced__copy {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-ced__bar > :not(.ot-ced__copy, .ot-head__gap) {
  flex: none;
}

.ot-ced__out {
  flex: none;
  max-height: 160px;
  padding: 8px 16px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  background: var(--fill);
  border-top: 1px solid var(--line);
}

.ot-ced__cmd {
  color: var(--muted);
}

.ot-ced__res {
  margin: 0;
  font: inherit;
  color: var(--ink);
  white-space: pre-wrap;
}
</style>
