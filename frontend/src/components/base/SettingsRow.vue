<script setup lang="ts">
/**
 * 知是设置页里的一行：左边是这一行在说什么（标签 + 一句说明），右边是控件
 * （默认插槽）。设置页行与行之间现在有三种写法——`.srow` 的 180px 标签列、
 * `.srow--field` 的 120px，以及各组件自己拿 flex 排的没有固定列的行；同一种东西
 * 三种样子。这一件把「标签在左、控件在右」收成一个。
 *
 * 宽度归这一件管：
 *
 * - 容器够宽（≥ 672px）时标签占固定一列，控件按 `width` 靠右。列宽 180px，和
 *   `styles/settings-card.css` 的 `.srow` 对齐。
 * - 容器窄于 672px 时标签换到控件上面，控件占满整行。判据是**这一行有多宽**，不是
 *   窗口有多宽（设置浮层的内容列、拖动的侧栏都会让窗口宽度答错），所以用容器查询：
 *   根元素声明 `container-type: inline-size`，按 AppPage 的注释补上 `width: 100%`
 *   ——行内尺寸包含之后宽度推不出来。
 *
 * `width` 是控件那一栏的宽：
 *
 * - `text` 340px：一行文本输入。
 * - `select-wide` 280px：带说明的下拉。
 * - `select` 192px：普通下拉。
 * - `code` 160px：短值（数量、标识符）。
 * - `list` 自由宽：控件自己撑满剩余的一栏（一列名单、一段多行文本）。
 * - `none` 满宽：这一行不留右边的栏，标签在上、控件占满整行（一块自成一体的控件）。
 *
 * `for` 给上时标签渲染成 `<label for>`；控件不是表单字段（一串读数加一颗按钮）时
 * 不给，标签就是一行字。
 */
import { computed } from 'vue'

export type SettingsRowWidth = 'text' | 'select-wide' | 'select' | 'code' | 'list' | 'none'

const props = withDefaults(
  defineProps<{
    label: string
    /** 标签下面那句说明。 */
    description?: string
    width?: SettingsRowWidth
    /** 控件的 id；给了标签就是 `<label for>`，不给就是一行字。 */
    for?: string
  }>(),
  { description: undefined, width: 'text', for: undefined }
)

// `for` 是关键字，模板表达式以它开头 babel 会当成 for 语句解析不了，所以在脚本里先算好。
const labelTag = computed(() => (props.for ? 'label' : 'span'))
const labelAttrs = computed(() => (props.for ? { for: props.for } : {}))
</script>

<template>
  <div class="base-settings-row" :class="`base-settings-row--${width}`">
    <div class="base-settings-row__grid">
      <component :is="labelTag" class="base-settings-row__label" v-bind="labelAttrs">
        <span class="base-settings-row__label-text">{{ label }}</span>
        <small v-if="description" class="base-settings-row__desc">{{ description }}</small>
      </component>
      <div class="base-settings-row__control">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* width: 100% 是 container-type 的代价：行内尺寸包含之后这一格的宽度不再由内容撑，
   要自己撑满父元素（AppPage 的 `.app-page__column--admin` 同款）。 */
.base-settings-row {
  width: 100%;
  container-type: inline-size;
}

.base-settings-row__grid {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 16px;
  align-items: center;
}

.base-settings-row__label {
  display: grid;
  gap: 2px;
  min-width: 0;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
}

.base-settings-row__desc {
  font-size: 12px;
  font-weight: 400;
  line-height: var(--lh-12);
  color: var(--muted);
}

.base-settings-row__control {
  display: flex;
  align-items: center;
  min-width: 0;
}

.base-settings-row--text .base-settings-row__control {
  max-width: 340px;
}
.base-settings-row--select-wide .base-settings-row__control {
  max-width: 280px;
}
.base-settings-row--select .base-settings-row__control {
  max-width: 192px;
}
.base-settings-row--code .base-settings-row__control {
  max-width: 160px;
}
.base-settings-row--list .base-settings-row__control {
  max-width: none;
}

/* 满宽：不留右边的栏，标签在上、控件占满整行。 */
.base-settings-row--none .base-settings-row__grid {
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
  align-items: start;
}
.base-settings-row--none .base-settings-row__control {
  max-width: none;
}

/* 容器窄：标签换到控件上面，控件占满整行。和窗口宽度无关。 */
@container (width < 672px) {
  .base-settings-row__grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
    align-items: start;
  }

  .base-settings-row__control {
    max-width: none;
    width: 100%;
  }
}
</style>
