<script setup lang="ts">
import { useI18n } from 'vue-i18n'

// 页顶那一条横条：**写**失败（红）或写成功了（绿），一次只画一条。
//
// 读失败**不在这里说** —— 模型表读不到画在表的列头下面、额度读不到画在那一段里、审计
// 读不到画在那张卡里。同一次失败说两遍，人会以为是两次。
//
// 两条的形态完全一样，只有左侧那道色标和记号的语义不同（`alert` / `status`），所以是
// 一件东西、两个分支，不是两件东西。
const props = defineProps<{
  /** 写操作失败的服务端原话。有它时压过 `notice`。 */
  error: string | null
  /** 写成功后的提示。 */
  notice: string | null
}>()

const emit = defineEmits<{ dismiss: [] }>()

const { t } = useI18n()
</script>

<template>
  <div v-if="props.error" class="amd__flash amd__flash--bad" role="alert">
    <v-icon icon="mdi-alert-circle-outline" size="16" class="amd__flashIcon" />
    <span class="amd__flashText">{{ props.error }}</span>
    <button
      type="button"
      class="amd__flashClose tap-target"
      :aria-label="t('models.notice.dismiss')"
      @click="emit('dismiss')"
    >
      <v-icon icon="mdi-close" size="14" />
    </button>
  </div>
  <div v-else-if="props.notice" class="amd__flash amd__flash--ok" role="status">
    <v-icon icon="mdi-check-circle-outline" size="16" class="amd__flashIcon" />
    <span class="amd__flashText">{{ props.notice }}</span>
    <button
      type="button"
      class="amd__flashClose tap-target"
      :aria-label="t('models.notice.dismiss')"
      @click="emit('dismiss')"
    >
      <v-icon icon="mdi-close" size="14" />
    </button>
  </div>
</template>

<style scoped>
/* 一条横条（写失败 / 提示）。**不是 `v-alert`**：那套默认样（大圆角、实色底、
   整块染色）在这一页的表格旁边像另一个产品。这里只留一条：左侧一道 3px 的色标
   说这是哪一类，其余全是这一页自己的底色与描边。 */
.amd__flash {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: var(--radius-md);
}

.amd__flash--bad {
  border-left-color: var(--danger);
}

.amd__flash--bad .amd__flashIcon {
  color: var(--danger);
}

.amd__flash--ok {
  border-left-color: var(--ok);
}

.amd__flash--ok .amd__flashIcon {
  color: var(--ok);
}

.amd__flashText {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.amd__flashClose {
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
  color: var(--muted);
  cursor: pointer;
}

.amd__flashClose:hover {
  background: var(--fill);
  color: var(--ink);
}

.amd__flashClose:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
