<script setup lang="ts">
// 面板底栏：左边写此刻回车会做什么，右边写还有哪几个键能用。桌面上才有这一栏——
// 手机上这几个键不存在。
//
// 三处要念同一个键的地方（顶栏的提示、结果行的 title、这里）都从 `keys.ts` 拿名字，
// 所以认哪台机器只做一次。
import { enterKey } from './keys'

import { t } from '@/i18n'

defineProps<{
  /** 面板在问一句话：回车是交答案。 */
  asking: boolean
  /** 此刻停在哪一件操作上（清单开着的时候）。 */
  actionTitle: string | undefined
  /** 此刻选中的那一条结果，回车做的那件事叫什么。 */
  verb: string | undefined
  /** 选中的那一条还支持进项目里搜。 */
  canSearchIn: boolean
  /** 选中的那一条还有别的事可做（Tab 展开）。 */
  hasMore: boolean
  /** 选中的那一条有地方可去：按住修饰键回车是新标签页。 */
  hasTarget: boolean
}>()
</script>

<template>
  <div class="palette__foot">
    <span class="palette__foot-main">
      <template v-if="asking">{{ t('navigation.palette.save') }}</template>
      <template v-else-if="actionTitle">{{ actionTitle }}</template>
      <template v-else-if="verb">{{ verb }}</template>
      <kbd>↵</kbd>
    </span>
    <span class="palette__keys">
      <span v-if="asking || actionTitle">{{ t('navigation.palette.back') }} <kbd>Esc</kbd></span>
      <template v-else-if="verb">
        <span v-if="canSearchIn">{{ t('navigation.palette.searchIn') }} <kbd>Tab</kbd></span>
        <span v-else-if="hasMore">{{ t('navigation.palette.more') }} <kbd>Tab</kbd></span>
        <span v-if="hasTarget">
          {{ t('navigation.palette.newTab') }} <kbd>{{ enterKey }}</kbd> <kbd>↵</kbd>
        </span>
      </template>
    </span>
  </div>
</template>

<style scoped>
.palette__foot {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.palette__foot-main {
  color: var(--muted);
}
.palette__keys {
  display: flex;
  gap: 12px;
}
.palette__foot kbd {
  display: inline-block;
  margin-left: 4px;
  min-width: 18px;
  padding: 0 4px;
  border: 1px solid var(--line-2);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-align: center;
}
</style>
