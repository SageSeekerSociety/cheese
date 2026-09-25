<script setup lang="ts">
// 页码条：一页 20 条，页码直接点，也能跳。
//
// 页码只画「首尾 + 当前左右各一页」，中间断开处放省略号 —— 板厚到三百道时页码条
// 不该长成一行数字。跳转框是给「翻到很后面」准备的：点二十下「下一页」不如输一个数。
import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    /** 计数用的量词。题目是「道」，列表里也可能是「人」。 */
    unit?: string
  }>(),
  { unit: '道' }
)

const emit = defineEmits<{ (e: 'update:page', value: number): void }>()

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const jump = ref('')

watch(
  () => props.page,
  () => (jump.value = '')
)

const slots = computed<(number | '…')[]>(() => {
  const last = pageCount.value
  const wanted = [1, props.page - 1, props.page, props.page + 1, last]
  const pages = [...new Set(wanted)].filter((p) => p >= 1 && p <= last).sort((a, b) => a - b)
  const out: (number | '…')[] = []
  pages.forEach((p, i) => {
    if (i > 0 && p - pages[i - 1] > 1) out.push('…')
    out.push(p)
  })
  return out
})

function go(page: number) {
  const target = Math.min(pageCount.value, Math.max(1, Math.trunc(page)))
  if (target !== props.page) emit('update:page', target)
}

function submitJump() {
  const n = Number(jump.value.trim())
  if (!Number.isFinite(n) || n < 1) {
    jump.value = ''
    return
  }
  go(n)
  jump.value = ''
}
</script>

<template>
  <nav v-if="pageCount > 1" class="pb" aria-label="分页">
    <span class="pb__count">共 {{ total }} {{ unit }} · 第 {{ page }} / {{ pageCount }} 页</span>

    <div class="pb__pages">
      <v-btn size="small" variant="text" :disabled="page === 1" prepend-icon="mdi-chevron-left" @click="go(page - 1)"
        >上一页</v-btn
      >
      <template v-for="(slot, i) in slots" :key="`${slot}-${i}`">
        <span v-if="slot === '…'" class="pb__gap">…</span>
        <v-btn
          v-else
          size="small"
          :variant="slot === page ? 'flat' : 'text'"
          :color="slot === page ? 'primary' : undefined"
          class="pb__num"
          :aria-current="slot === page ? 'page' : undefined"
          @click="go(slot)"
          >{{ slot }}</v-btn
        >
      </template>
      <v-btn
        size="small"
        variant="text"
        :disabled="page === pageCount"
        append-icon="mdi-chevron-right"
        @click="go(page + 1)"
        >下一页</v-btn
      >
    </div>

    <div class="pb__jump">
      <span>跳至</span>
      <v-text-field
        v-model="jump"
        autocomplete="off"
        type="number"
        density="compact"
        variant="outlined"
        hide-details
        class="pb__input"
        :placeholder="String(page)"
        @keyup.enter="submitJump"
      />
      <span>页</span>
      <v-btn size="small" variant="outlined" @click="submitJump">跳转</v-btn>
    </div>
  </nav>
</template>

<style scoped lang="scss">
.pb {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pb__count {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
}

.pb__pages {
  display: flex;
  gap: 2px;
  align-items: center;
}

.pb__num {
  min-width: 34px;
  font-variant-numeric: tabular-nums;
}

.pb__gap {
  padding: 0 4px;
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.pb__jump {
  display: flex;
  gap: 6px;
  align-items: center;
  /* 「跳至 __ 页 / 跳转」整块用正文字色，而不是灰色：它是给操作的人读的，
     灰到 0.55 在一排页码里像是禁用状态。 */
  color: var(--text);
  font-size: 0.78rem;
}

.pb__input {
  width: 74px;
}

/* 数字框的上下箭头在这一行里显得挤，去掉 —— 跳转靠输入和回车。 */
.pb__input :deep(input) {
  appearance: textfield;
  text-align: center;
}

/* 占位符里放的是当前页码，所以它也得是正文字色 —— 灰色的默认值看着像「这里还没填」。 */
.pb__input :deep(input)::placeholder {
  color: var(--text);
  opacity: 1;
}
</style>
