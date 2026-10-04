<script setup lang="ts">
// 后台那条横幅提示：写失败、读失败、保存成功，都是这一条。
//
// 它此前是**三份逐字相同的拷贝**（`.amd__flash` / `.asp__flash` / `.am__flash`），外加
// 飞书页一行裸红字和看板自绘的一块 —— 同一件事有四种长相，切分区时提示条的形状跟着变。
// 收成一个组件后只有一个地方会错。
//
// 配色按 `docs/design-system.md` 的「状态色三件套」：`--x` 是标记（左边那道色条和图标）、
// `--x-ink` 是文字、`--x-wash` 是底色。错误和成功各用各的一套，不能互换 —— 把 `--danger`
// 当文字色是这棵树上最常见的缺陷，浅色主题下它在白底只有 4.4:1 左右。
defineOptions({ name: 'AdminFlash' })

withDefaults(
  defineProps<{
    /** `error` 说这次没成，`ok` 说这次成了。 */
    tone: 'error' | 'ok'
    text: string
    /** 给一个就画右上角的叉。读失败那类提示不该给：叉只会让人以为撤掉了失败。 */
    dismissAria?: string
  }>(),
  { dismissAria: undefined }
)

const emit = defineEmits<{ dismiss: [] }>()
</script>

<template>
  <div class="afl" :class="`afl--${tone}`" :role="tone === 'error' ? 'alert' : 'status'">
    <v-icon
      class="afl__icon"
      :icon="tone === 'error' ? 'mdi-alert-circle-outline' : 'mdi-check-circle-outline'"
      size="16"
      aria-hidden="true"
    />
    <span class="afl__text">{{ text }}</span>
    <button
      v-if="dismissAria"
      type="button"
      class="afl__close tap-target"
      :aria-label="dismissAria"
      @click="emit('dismiss')"
    >
      <v-icon icon="mdi-close" size="14" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.afl {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  line-height: var(--lh-13);
}

.afl--error {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.afl--ok {
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.afl__icon {
  flex: 0 0 auto;
  color: var(--danger);
}

.afl--ok .afl__icon {
  color: var(--ok);
}

.afl__text {
  flex: 1 1 auto;
  min-width: 0;
}

.afl__close {
  /* 相对定位给 .tap-target：这一颗只有 ~18px，手指要点得中（§3.6）。 */
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  padding: 2px;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  color: inherit;
  cursor: pointer;
  opacity: 0.72;
}

.afl__close:hover {
  background: var(--fill);
  opacity: 1;
}

.afl__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
