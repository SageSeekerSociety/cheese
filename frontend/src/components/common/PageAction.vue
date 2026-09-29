<script setup lang="ts">
// 一页的一个操作（刷新、新建、邀请……）。桌面上它就是页头里的那颗按钮；手机上
// （< 960px）它不画在页面里，而是交给顶栏：标了 `primary` 的是顶栏右边一颗图标按钮，
// 其余的收进顶栏的 ⋯（AdaptiveMenu，手机上是底部动作面板）。
//
//   <PageAction label="新建" icon="mdi-plus" primary color="primary" @click="startNew" />
//   <PageAction label="刷新" icon="mdi-refresh" :loading="loading" @click="load" />
//
// `label` 和 `icon` 两端都用：桌面上是按钮上的字，手机上是图标和面板里那一行。其余
// 属性（color、variant、prepend-icon、append-icon、class……）原样交给桌面那颗 v-btn，
// 所以桌面长什么样由页面自己说了算。`icon-only` 让桌面上也只画图标。
//
// 放在 ProjectPage 的 #actions 里，或者任何一页自己的页头里都行：它自己知道现在是
// 哪一端。一页只标一个 primary；标了几个，顶栏只画第一个，其余进 ⋯。
import type { RouteLocationRaw } from 'vue-router'

import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { registerTopBarAction, unregisterTopBarAction } from './topBarActions'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    label: string
    /** mdi 图标名：手机顶栏与面板里用；`icon-only` 时桌面也用。 */
    icon: string
    /** 手机上放在顶栏右边的那一颗；不标就进 ⋯。 */
    primary?: boolean
    /** 桌面上也只画图标（字进 aria-label）。 */
    iconOnly?: boolean
    danger?: boolean
    loading?: boolean
    disabled?: boolean
    to?: RouteLocationRaw
  }>(),
  { primary: false, iconOnly: false, danger: false, loading: false, disabled: false, to: undefined }
)

const emit = defineEmits<{ click: [] }>()

const { mdAndUp } = useDisplay()

// 登记的先后就是 ⋯ 里的先后：按组件建出来的顺序给号，也就是它在模板里的顺序。
const id = nextId++
const active = ref(false)

watch(
  [active, mdAndUp],
  ([on, desktop]) => {
    if (on && !desktop)
      registerTopBarAction(id, () => ({
        key: `page-action-${id}`,
        label: props.label,
        icon: props.icon,
        primary: props.primary,
        danger: props.danger,
        loading: props.loading,
        disabled: props.disabled,
        to: props.to,
        onSelect: () => emit('click'),
      }))
    else unregisterTopBarAction(id)
  },
  { immediate: true }
)

// 保活的页面（CalendarView 那几页）切走时组件还在，但这一页已经不在屏幕上。
onMounted(() => (active.value = true))
onActivated(() => (active.value = true))
onDeactivated(() => (active.value = false))
onBeforeUnmount(() => {
  active.value = false
  unregisterTopBarAction(id)
})
</script>

<script lang="ts">
let nextId = 1
</script>

<template>
  <template v-if="mdAndUp">
    <v-btn
      v-if="props.iconOnly"
      v-bind="$attrs"
      :icon="props.icon"
      :to="props.to"
      :loading="props.loading"
      :disabled="props.disabled"
      :aria-label="props.label"
      :title="props.label"
      @click="emit('click')"
    />
    <v-btn
      v-else
      v-bind="$attrs"
      :to="props.to"
      :loading="props.loading"
      :disabled="props.disabled"
      @click="emit('click')"
    >
      {{ props.label }}
    </v-btn>
  </template>
</template>
