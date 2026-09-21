<script setup lang="ts">
/**
 * AdminGrid.vue — 管理台两张表（反馈队列、成员名单）共用的壳。
 *
 * 抽出来的理由不是省几行：**这一层有三件容易写错、而且写错之后两张表会以不同
 * 方式错的事**，它们各需要一个地方安放。
 *
 *   1. **表头常驻**。一屏十七行，滚到第八行还要记得「第 7 列是什么」的话，这一列
 *      就等于没有。做法是 `position: sticky; top: 0` 挂在 `th` 上，而滚动容器必须是
 *      这一层自己（`.agrid__scroll`）—— 让外层的卡片去滚，sticky 的参照物就变了。
 *      **硬坑：卡片不能加 `overflow: hidden`**，那会让 sticky 直接失效（父级有了新的
 *      滚动/裁剪上下文）。圆角因此改由首末行的单元格自己画，见下面那四条 longhand。
 *   2. **列宽只有一份**。`table-layout: fixed` + `<colgroup>`：列宽写在 colgroup 上，
 *      表头和表体各画一遍的话，两边迟早差一档，表现是表头文字和它下面那一列对不上。
 *   3. **加载骨架必须是同一张表**。骨架换成一个独立的 div 形态的话，数据到货那一刻
 *      整张表会重排一次 —— 而骨架的全部意义就是不重排。所以这里的骨架是**真的
 *      `<tr>`**，走同一份 colgroup，行高和真行一样。这也是它没有复用
 *      `LoadingSkeleton.vue` 那个通用骨架的原因：那个文件的规矩是「一种形态只对
 *      一样东西负责」，而这一样东西（表格行）只有表格画得出来。
 *
 * 因此 `cols` 是这一层的核心参数：每列一个宽度，`null` 表示「这一列吃剩下的」。
 * `table-layout: fixed` 下没有宽度的列会平分剩余空间，所以**只给一列传 `null`**，
 * 否则剩下的那一份会被平摊成几列，密度就散了（反馈表里那一列是标题，成员表里是
 * 添加信息）。
 */

const props = withDefaults(
  defineProps<{
    /** 每列的宽度，如 `'76px'`；`null` = 自适应（吃满剩下的）。 */
    cols: (string | null)[]
    /** 给读屏的表名，如「反馈列表」。表格没有可读的名字时，读屏只会念「表格」。 */
    label: string
    /** 骨架里每根骨头占该格宽度的比例，按列算。省略时用一组通用值。 */
    boneWidths?: string[]
    /** 首次加载中。**只在手上一条都没有时传 true** —— 已经有内容时换骨架，整表
     *  会闪一下，那是比「旧内容多停半秒」更糟的手感（那种情况传 `busy`）。 */
    loading?: boolean
    /** 加载完了、这一栏一条都没有时显示的话（§8.1：一律「暂无 X」，不带句号）。
     *  `null` = 有内容。 */
    empty?: string | null
    skeletonRows?: number
    /** 正在取下一页，但手上还留着上一页。不换骨架，只把表体压暗一档。 */
    busy?: boolean
  }>(),
  { loading: false, empty: null, skeletonRows: 10, busy: false, boneWidths: undefined }
)

/** 通用的骨头形状：长 - 中 - 短 - 小 循环。每一种宽度的骨头一样长的话，骨架看着
 *  像一条条对齐的横线，反而比空白更晃眼。 */
const BONE_FALLBACK = ['86%', '64%', '72%', '44%', '58%', '50%']

const bone = (column: number): string => props.boneWidths?.[column] ?? BONE_FALLBACK[column % BONE_FALLBACK.length]
</script>

<template>
  <div class="agrid" :class="{ 'agrid--busy': busy }">
    <div class="agrid__scroll">
      <table class="agrid__table" :aria-label="label" :aria-busy="loading || busy">
        <colgroup>
          <col v-for="(width, i) in cols" :key="i" :style="width ? { width } : undefined" />
        </colgroup>
        <thead class="agrid__head">
          <slot name="head" />
        </thead>
        <tbody class="agrid__body">
          <template v-if="loading">
            <tr v-for="i in skeletonRows" :key="`skel-${i}`" class="agrid__row">
              <td v-for="(_, c) in cols" :key="c" class="agrid__cell">
                <span class="agrid__bone" :style="{ width: bone(c) }" />
              </td>
            </tr>
          </template>
          <tr v-else-if="empty" class="agrid__row">
            <td :colspan="cols.length" class="agrid__cell agrid__none">{{ empty }}</td>
          </tr>
          <slot v-else />
        </tbody>
      </table>
    </div>
    <div v-if="$slots.foot" class="agrid__foot">
      <slot name="foot" />
    </div>
  </div>
</template>

<style scoped>
.agrid {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  /* 没有 `overflow: hidden` —— 见文件开头第 1 条，加了表头就不再常驻。 */
  min-height: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.agrid__scroll {
  flex: 1 1 auto;
  min-height: 0;
  /* 纵向：一屏装不下的行交给这里滚（表头因此可以 sticky）。
     横向：窄屏（约 1280 以下）时整张表有一条 `min-width`，靠这一条横着滚，
     而不是把列挤窄到读不出来。 */
  overflow: auto;
}

.agrid__table {
  width: 100%;
  min-width: 1080px;
  /* 列宽只认 `<colgroup>`：固定布局下单元格里长出来的内容（长标题、长 handle）
     不会把列撑开，溢出由 `text-overflow: ellipsis` 收掉。 */
  table-layout: fixed;
  border-collapse: separate;
  border-spacing: 0;
}

.agrid__head :deep(th) {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 12px;
  /* sticky 时**不能透明**，否则行会从表头文字底下穿过去。 */
  background: var(--surface);
  border-bottom: 1px solid var(--line-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  text-align: left;
  white-space: nowrap;
}

.agrid__cell {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}

/* 表头那一条线属于表头，最后一行不画线 —— 画了会和卡片自己的描边挤成两条。 */
.agrid__body > tr:last-child > .agrid__cell {
  border-bottom: 0;
}

/* 首末行的圆角。卡片没加 `overflow: hidden`，所以这两处得自己画：不画的话，
   悬停时的底色是一个方角，会盖住卡片圆角那一小块（描边的弧还在，里面却填成了
   方的）。四条都是 longhand —— stylelint 按字面比较圆角值，简写会被判成新违规。 */
.agrid__table > thead > tr:first-child > :deep(th:first-child) {
  border-top-left-radius: var(--radius-lg);
}
.agrid__table > thead > tr:first-child > :deep(th:last-child) {
  border-top-right-radius: var(--radius-lg);
}
.agrid__body > tr:last-child > .agrid__cell:first-child {
  border-bottom-left-radius: var(--radius-lg);
}
.agrid__body > tr:last-child > .agrid__cell:last-child {
  border-bottom-right-radius: var(--radius-lg);
}

.agrid__row {
  /* 悬停只换底色、不位移（`.claude/rules/frontend.md`）。 */
  transition: background-color 0.12s ease;
}

.agrid__row:hover {
  background: var(--fill);
}

.agrid__none {
  padding: 32px 12px;
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: center;
}

/* 取下一页时把上一页压暗。用透明度而不是换骨架：内容还在，只是不新鲜了。 */
.agrid--busy .agrid__body {
  opacity: 0.55;
  transition: opacity 0.12s ease;
}

.agrid__bone {
  display: block;
  height: 12px;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}

.agrid__foot {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 40px;
  padding: 0 12px;
  border-top: 1px solid var(--line);
}
</style>
