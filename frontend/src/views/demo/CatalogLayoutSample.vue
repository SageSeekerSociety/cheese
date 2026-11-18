<script setup lang="ts">
/**
 * 布局层试验台里的一格内容：一段能滚的列表，底下一个输入框；`frame` 时换成一个 iframe。
 * 换格、收起、放大、换宽窄之后，打的字、滚动位置、iframe 都该还在原样。
 */
import { ref } from 'vue'

withDefaults(defineProps<{ label: string; rows?: number; frame?: boolean }>(), { rows: 40, frame: false })

const text = ref('')
</script>

<template>
  <iframe v-if="frame" class="sample__frame" :title="label" srcdoc="<p style='font:14px sans-serif'>iframe</p>" />
  <div v-else class="sample">
    <div class="sample__list" :data-sample-list="label">
      <div v-for="i in rows" :key="i" class="sample__row">{{ label }} 第 {{ i }} 行</div>
    </div>
    <textarea
      v-model="text"
      class="sample__input"
      autocomplete="off"
      :aria-label="label"
      :data-sample-input="label"
      :placeholder="`在${label}里打几个字`"
    />
  </div>
</template>

<style scoped>
.sample {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.sample__list {
  flex: 1 1 auto;
  min-height: 0;
  padding: 8px 12px;
  overflow: auto;
  font-size: 13px;
  line-height: var(--lh-13);
}

.sample__row {
  padding: 2px 0;
}

.sample__input {
  flex: 0 0 auto;
  height: 64px;
  padding: 8px 12px;
  font: inherit;
  font-size: 14px;
  color: var(--text);
  resize: none;
  background: var(--fill);
  border: 0;
  border-top: 1px solid var(--line);
  outline: none;
}

.sample__frame {
  width: 100%;
  height: 100%;
  border: 0;
}
</style>
