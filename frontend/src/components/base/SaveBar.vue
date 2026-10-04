<script setup lang="ts">
/**
 * 设置区块底下那一条：有改动时从内容的下边缘展开，写着「有未保存的改动」，
 * 右边并排撤销与保存两颗；保存完（或还剩失败）时收起按钮，只留一行
 * `SaveStatus` 的回执，再淡出。docs/design-system.md §3.11。
 *
 * 它是从 `views/user/settings/Profile.vue` 的 footer 抽出来的：那一条的形状
 * （网格展开、`--canvas` 底、上边一道 `--line`）在设置页里重复，这里收成一个。
 *
 * 谁决定展开：`dirty`（本地改动）、`saving` / `saved` / `error`（`useSaveState`
 * 给的四态）。这四样的来源是 `useSaveState`，这一件只画。
 *
 * 文字都可由调用方给，默认是全局的「有未保存的改动」/「撤销」/「保存」；已经有
 * 自己说法的页面（如 Profile）把自己的传进来，不改它的文案。
 */
import BaseButton from './BaseButton.vue'
import SaveStatus from './SaveStatus.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 本地草稿和已保存的值不同：展开、放出撤销与保存。 */
    dirty: boolean
    saving?: boolean
    saved?: boolean
    error?: string | null
    /** 表单不合法时禁掉保存那颗。 */
    disabled?: boolean
    note?: string
    revertLabel?: string
    saveLabel?: string
    savedText?: string
    failedText?: string
  }>(),
  {
    saving: false,
    saved: false,
    error: null,
    disabled: false,
    note: undefined,
    revertLabel: undefined,
    saveLabel: undefined,
    savedText: undefined,
    failedText: undefined,
  }
)

defineEmits<{ revert: []; save: [] }>()

// 有按钮时才排按钮（保存中/已保存/失败还没改回时，`dirty` 可能仍为真）。
const showButtons = () => props.dirty
</script>

<template>
  <div>
    <Transition name="save-bar-reveal">
      <div v-if="dirty || saving || saved || !!error" class="save-bar__reveal">
        <div class="save-bar__clip">
          <div class="save-bar__rail">
            <SaveStatus
              :saving="saving"
              :saved="saved"
              :error="error"
              :saved-text="savedText"
              :failed-text="failedText"
            />
            <span v-if="dirty && !saving && !saved && !error" class="save-bar__note">
              {{ note ?? t('global.saveState.unsaved') }}
            </span>
            <div class="save-bar__spacer" />
            <template v-if="showButtons()">
              <BaseButton :disabled="saving" @click="$emit('revert')">
                {{ revertLabel ?? t('global.saveState.revert') }}
              </BaseButton>
              <BaseButton kind="primary" type="button" :disabled="disabled" :loading="saving" @click="$emit('save')">
                {{ saveLabel ?? t('global.save') }}
              </BaseButton>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* 展开/收起写在网格行高上，收起时零高，不占位置。 */
.save-bar__reveal {
  display: grid;
  grid-template-rows: 1fr;
}

.save-bar__clip {
  min-height: 0;
  overflow: hidden;
}

.save-bar-reveal-enter-active {
  transition:
    grid-template-rows var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}

.save-bar-reveal-leave-active {
  transition:
    grid-template-rows var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}

.save-bar-reveal-enter-from,
.save-bar-reveal-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}

.save-bar__rail {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 16px 24px;
  background: var(--canvas);
  border-top: 1px solid var(--line);
}

.save-bar__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.save-bar__spacer {
  flex-grow: 1;
}

/* 手机外壳（窄于 768 —— 共享 token，见 `styles/breakpoints.scss`、`settings-card.css`）。 */
@media (max-width: 767.98px) {
  .save-bar__rail {
    padding: 12px 16px;
  }
}
</style>
