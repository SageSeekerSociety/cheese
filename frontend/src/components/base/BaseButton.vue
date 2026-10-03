<script setup lang="ts">
/**
 * 知是的按钮。业务代码写按钮只用它，不直接写 `<v-btn>`
 * （docs/design-system.md §3.6「按钮」；琥珀的用法见 §1.6）。
 *
 * 它只回答两个问题：**这颗按钮在这块区域里是什么角色**（`kind`），**多大**（`size`）。
 * 颜色、样式变体、圆角、字重都由角色推出来，调用处不再写 `variant` / `color`：
 * 同一个角色在全产品长一个样子，是这一层存在的全部理由。
 *
 * - `primary`   一块区域里让事情往下走的那一颗（提交、保存、下一步）。琥珀实心。
 *               同一组并排按钮里只有一颗。
 * - `secondary` 独立出现、要被看见、但不是主操作的按钮：设置行里的「修改」「添加」、
 *               「连接这台电脑」、一组下载里除第一颗外的其余几颗。描边。
 * - `ghost`     其余一切轻量操作：弹窗和表单里的「取消」「返回」、工具条、列表行、
 *               卡片角落里的操作，以及纯图标按钮。无底无框。
 * - `danger`    直接生效、不再确认的破坏性操作。红字无底。会先弹确认的「移除」入口
 *               用 `ghost`，红色留给确认弹窗里那一颗：`solid` 实心红。
 *
 * 尺寸：`sm` 28px（列表行、工具条、卡片内），`md` 36px（默认，表单、弹窗），
 * `lg` 44px（门口页面的单个大按钮、手机上整行宽的提交）。
 * 纯图标按钮传 `icon`，必须同时给 `aria-label`（或 `title`），否则读屏只念「按钮」。
 *
 * 其余属性（`to`、`href`、`type`、`loading`、`disabled`、`block`、
 * `prepend-icon`、`append-icon`、事件）原样透传给 `<v-btn>`。
 */
import { computed, useAttrs } from 'vue'

export type ButtonKind = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    kind?: ButtonKind
    size?: ButtonSize
    /** 纯图标按钮：mdi 图标名。给了它就不渲染默认插槽里的文字。 */
    icon?: string
    /** 只对 `danger` 有效：实心红，确认弹窗里那一颗。 */
    solid?: boolean
  }>(),
  { kind: 'ghost', size: 'md', icon: undefined, solid: false }
)

defineOptions({ inheritAttrs: false })
const attrs = useAttrs()

// 角色 → Vuetify 的 variant 与 color。主题色 `primary` 就是 --accent（plugins/vuetify.ts）。
const look = computed(() => {
  switch (props.kind) {
    case 'primary':
      return { variant: 'flat', color: 'primary' } as const
    case 'secondary':
      return { variant: 'outlined', color: undefined } as const
    case 'danger':
      return props.solid
        ? ({ variant: 'flat', color: 'error' } as const)
        : // 不给 color：Vuetify 会把它变成带 !important 的 .text-error，压掉下面的
          // --danger-ink，字就成了只配当标记色的 --danger。
          ({ variant: 'text', color: undefined } as const)
    default:
      return { variant: 'text', color: undefined } as const
  }
})

// sm/md/lg 对上 Vuetify 的 small(28)/default(36)/large(44)。
const vSize = computed(() => ({ sm: 'small', md: 'default', lg: 'large' })[props.size])

if (import.meta.env.DEV && props.icon && !attrs['aria-label'] && !attrs.title) {
  console.warn(`[BaseButton] icon-only button "${props.icon}" needs aria-label or title`)
}
</script>

<template>
  <!-- eslint-disable-next-line vue/no-restricted-syntax -- the one place v-btn belongs -->
  <v-btn
    v-bind="attrs"
    :variant="look.variant"
    :color="look.color"
    :size="vSize"
    :icon="icon"
    class="base-btn"
    :class="[`base-btn--${kind}`, `base-btn--${size}`, { 'base-btn--icon': icon }]"
  >
    <!-- 纯图标时不能给默认插槽：v-btn 一见到默认插槽就不画 icon 了。 -->
    <template v-if="!icon" #default><slot /></template>
  </v-btn>
</template>

<style scoped>
/* 文字按钮统一 13/14px、500 字重；图标按钮里的图标统一 18/20px。
   这几条以前散在每个调用处的 class 和 style 里。 */
.base-btn--sm {
  font-size: 13px;
}
.base-btn--sm:not(.base-btn--icon) {
  padding-inline: 10px;
}
.base-btn--md,
.base-btn--lg {
  font-size: 14px;
}
.base-btn--sm.base-btn--icon :deep(.v-icon) {
  font-size: 18px;
}
.base-btn--md.base-btn--icon :deep(.v-icon),
.base-btn--lg.base-btn--icon :deep(.v-icon) {
  font-size: 20px;
}

/* 次要：描边用 --line-2，字用 --text。Vuetify 的 outlined 默认拿 currentColor
   画框，框和字一样深，并排时比主按钮还抢眼。 */
.base-btn--secondary {
  color: var(--text);
  border-color: var(--line-2);
}
.base-btn--secondary:hover {
  background: var(--fill);
}

/* 轻量：字用 --muted，悬停变 --text 并垫 --fill。图标按钮同样。 */
.base-btn--ghost {
  color: var(--muted);
}
.base-btn--ghost:hover {
  color: var(--text);
}

/* 危险（非实心）：字用 --danger-ink，满足 4.5:1；--danger 本身只是标记色。 */
.base-btn--danger:not(.v-btn--variant-flat) {
  color: var(--danger-ink);
}
</style>
