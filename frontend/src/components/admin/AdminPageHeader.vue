<script setup lang="ts">
// 后台每一页的页头：标题一行、可选的一句说明、右侧工具槽。
//
// 之前五页各写一份（`.qpage__head` / `.ad__head` / `.amd__head` / `.am__head` /
// `v-container + h1.text-h5`），字号、内边距和分割线各不相同，切分区时页头会跳。
// 这里定下一份：56px 高的标题行、24px 左右内边距（窄屏 16px）、白底、底部一条 --line。
// 说明文字放在标题行下面而不是旁边——它是给第一次来的人看的，不该跟工具抢位置。
//
// 底边用 `--line` 而不是 `--line-2`：这条线是**白带的外轮廓**（白带底下直接是灰画布），
// 不是白带内部的分隔。内部那种本来就不画 —— 一条白带只有一道底边。
//
// 下面还接着一条筛选/操作区、或者整条白带由外层画线时挂上 `aph--flush`（`AdminPageShell`
// 会自己挂）：线只画在白带最下面，页头自己不再画，免得两条叠在一起。
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
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.aph--flush {
  border-bottom: 0;
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
