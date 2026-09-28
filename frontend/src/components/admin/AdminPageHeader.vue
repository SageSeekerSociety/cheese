<script setup lang="ts">
// 后台每一页的页头：标题一行、可选的一句说明、右侧工具槽。
//
// 之前五页各写一份（`.qpage__head` / `.ad__head` / `.amd__head` / `.am__head` /
// `v-container + h1.text-h5`），字号、内边距和分割线各不相同，切分区时页头会跳。
// 这里定下一份：56px 高的标题行、24px 左右内边距（窄屏 16px）、底部一条 --line-2。
// 说明文字放在标题行下面而不是旁边——它是给第一次来的人看的，不该跟工具抢位置。
defineOptions({ name: 'AdminPageHeader' })

defineProps<{
  title: string
  /** 一句话讲这一页管什么。省略就只有标题行。 */
  sub?: string
}>()
</script>

<template>
  <header class="aph">
    <div class="aph__row">
      <h1 class="t-console-title aph__title">{{ title }}</h1>
      <div v-if="$slots.tools" class="aph__tools">
        <slot name="tools" />
      </div>
    </div>
    <p v-if="sub" class="aph__sub">{{ sub }}</p>
    <div v-if="$slots.default" class="aph__extra">
      <slot />
    </div>
  </header>
</template>

<style scoped>
.aph {
  flex: 0 0 auto;
  padding: 12px 24px;
  border-bottom: 1px solid var(--line-2);
}

.aph__row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 32px;
}

.aph__title {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aph__tools {
  display: flex;
  flex: 0 1 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

/* 上限按 em 而不是 ch 算：`ch` 是「0」的宽度，约半个汉字，72ch 只装得下三十几个
   字，一句五十字的说明就会折成两行、末行只剩一两个字。`text-wrap: pretty` 再兜一层，
   真要折行时不留孤字。 */
.aph__sub {
  margin: 4px 0 0;
  max-width: 60em;
  text-wrap: pretty;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.aph__extra {
  margin-top: 12px;
}

@media (max-width: 700px) {
  .aph {
    padding: 12px 16px;
  }

  .aph__title {
    font-size: 19px;
    line-height: 28px;
  }
}
</style>
