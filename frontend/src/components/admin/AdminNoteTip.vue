<script setup lang="ts">
import { useI18n } from 'vue-i18n'

// 口径注脚的统一归宿：一个 info 图标按钮 + tooltip。
//
// 后台每个信息块都有一句「这个数是怎么算的」的口径注（成本口径、机器「不是
// 在线数」之类）。以前它们以 12.5px 灰字散行叠在块底，一屏七八条就成了注脚
// 墙——全在等于没有重点。现在散行注脚一律收进这里，防误读级的常驻注每屏至
// 多保留一条。
//
// open-on-click 让触屏点得开（hover/focus 是 v-tooltip 的默认行为，桌面已覆盖）。
const { t } = useI18n()

defineProps<{
  /** 口径正文。 */
  text: string
}>()
</script>

<template>
  <v-tooltip :text="text" location="top" open-on-click :open-delay="0">
    <template #activator="{ props: tip }">
      <button type="button" class="ant" v-bind="tip" :aria-label="t('feedback.dashboard.noteTip.aria')">
        <span class="ant__icon mdi mdi-information-outline" aria-hidden="true" />
      </button>
    </template>
  </v-tooltip>
</template>

<style scoped>
.ant {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
  transition: color 0.12s ease;
}

.ant:hover,
.ant:focus-visible {
  color: var(--text);
}

.ant:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.ant__icon {
  font-size: 14px;
  line-height: 1;
}
</style>
