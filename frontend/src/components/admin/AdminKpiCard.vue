<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

import { computed } from 'vue'

// 看板 KPI 行里的一张卡（263×92，§4.2）。
//
// **有没有 `to` 决定这张卡是不是一个可点的东西**，而且两种卡差的不是「像不像链接」，
// 是可点的那三件实事：`cursor: pointer`、hover 底色、进 Tab 顺序。只给它一半 —— 指针
// 变了形、指针放上去有反应，按下去却什么都不发生 —— 比完全不给更糟：人下一次就不再
// 相信这一屏里任何指针的含义了。所以不可点的那张这三样一样都不写（不是「写了但看不
// 出来」，是根本不写）。
//
// 数字由调用方格式化好再进来（`value` 是字符串）：千分位、「12.4 万」这类写法是看板
// 的口径，不是一张卡的口径。这里只负责把它画成 23px / `.t-num` 那一套（§6.1 的
// `.t-console-title`，它定义的一处用处就是「看板 KPI 大数字」）。
//
// `value` 是空串时画长破折号，不画 0：0 是**读出来了、这周确实是零**，空串是**没读到**。
// 两者在屏幕上必须长得不一样，否则「接口挂了」会被读成「一切正常，只是没人提交」。

const props = withDefaults(
  defineProps<{
    /** 「待分诊」这类短标签。 */
    label: string
    /** 已格式化好的数字串。空串 = 拿不到值。 */
    value: string
    /** 可选后缀，如「条」。 */
    unit?: string
    /** 有去向才是链接。见文件开头。 */
    to?: RouteLocationRaw
    /** 首次加载。骨架的形状和真卡完全一样，到货那一刻不重排。 */
    loading?: boolean
  }>(),
  { loading: false }
)

const shown = computed(() => (props.value.trim() === '' ? '—' : props.value))
</script>

<template>
  <!-- 骨架画成 `<div>` 而不是链接：一张还没到货的卡不是一个目的地，把它放进 Tab 顺序
       等于让人在数字出现之前先 Tab 到它一次。 -->
  <div v-if="loading" class="akpi">
    <v-skeleton-loader type="text" class="akpi__skel akpi__skel--label" />
    <v-skeleton-loader type="text" class="akpi__skel akpi__skel--value" />
  </div>

  <router-link v-else-if="to" :to="to" class="akpi akpi__link">
    <span class="akpi__label t-eyebrow-read">{{ label }}</span>
    <span class="akpi__value">
      <span class="akpi__num t-console-title t-num">{{ shown }}</span>
      <span v-if="unit" class="akpi__unit t-dense">{{ unit }}</span>
    </span>
  </router-link>

  <div v-else class="akpi">
    <span class="akpi__label t-eyebrow-read">{{ label }}</span>
    <span class="akpi__value">
      <span class="akpi__num t-console-title t-num">{{ shown }}</span>
      <span v-if="unit" class="akpi__unit t-dense">{{ unit }}</span>
    </span>
  </div>
</template>

<style scoped>
/* 宽高写死是这一行宽度算式的前提：4 × 263 + 3 × 16 = 1100（§4.2）。让卡片自己去量内容
   的话，四张卡会各自取一个数，那一行就不再是 1100 —— 而 1100 是这一页的列宽。 */
.akpi {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
  box-sizing: border-box;
  width: 263px;
  height: 92px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.akpi__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 单位跟着数字的基线坐，不跟行盒：`.t-console-title` 的行高是 33px，单位若按行盒居中
   会掉到数字底部以下，读起来像另一个字段。 */
.akpi__value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

/* 有 `to` 才有的三件事。hover 包在 `(hover: hover)` 里：触屏点过之后 `:hover` 会一直
   粘着，那张卡看起来像被选中了 —— 而这一页没有「选中一张 KPI 卡」这回事。 */
.akpi__link {
  text-decoration: none;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

@media (hover: hover) and (pointer: fine) {
  .akpi__link:hover {
    background: var(--fill);
  }
}

/* 骨架。`v-skeleton-loader` 的骨头默认带 16px 外边距和 12px 高，放在 92px 的卡里会把
   两条挤成一条；改成本地尺寸。底色用 `--fill-2`（§7.1 给骨架条指定的那一档），
   Vuetify 默认的 `--v-theme-on-surface` 在深浅两个主题里都不是这一档。 */
.akpi__skel--label {
  width: 56px;
}

.akpi__skel--value {
  width: 72px;
}

.akpi__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.akpi__skel--value :deep(.v-skeleton-loader__text) {
  height: 23px;
}
</style>
