<script setup lang="ts">
// 删频道前的二次确认（清单第 2 条）。删掉的是一个频道里的一切——消息、任务、文档，
// 都不能恢复，所以不点「删除频道」就算数：得把频道名原样打一遍。这一层挡的是手滑，
// 不是权限；能不能删由后端再判一次。
//
// 删本身在外面做：这里只收名字、只在打对了以后让人按下去。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 要删的频道叫什么——页头上显示的那一个。 */
  name: string
  /** 正在删：按钮转圈，这时候关不掉。 */
  busy?: boolean
}>()
const emit = defineEmits<{ (e: 'confirm'): void }>()
const open = defineModel<boolean>({ required: true })

const typed = ref('')
// 每次打开都从空开始：上一次打对过的名字不能替这一次作数。
watch(open, (now) => {
  if (now) typed.value = ''
})

// 没有名字的频道（还没起名的草稿）不给删：空输入等于没确认，那一格挡不住手滑。
const sure = computed(() => !!props.name && typed.value.trim() === props.name)

function confirm() {
  if (sure.value) emit('confirm')
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    primary-danger
    :title="t('work.channel.delete.title', { name: name })"
    :primary-label="t('work.channel.delete.confirm')"
    :primary-disabled="!sure"
    :primary-loading="busy"
    :close-disabled="busy"
    @primary="confirm"
  >
    <p class="t-body delete-channel__body">{{ t('work.channel.delete.body') }}</p>
    <!-- 这个弹窗只为一件事而开：把名字打一遍。打开它本身就是用户的动作（不是页面自己
         弹出来的），焦点落在唯一那一格上，正是这个规则要挡的那种「没打招呼就抢焦点」。 -->
    <!-- eslint-disable vuejs-accessibility/no-autofocus -- 见上一句 -->
    <v-text-field
      v-model="typed"
      class="delete-channel__field"
      :label="t('work.channel.delete.nameLabel')"
      :hint="t('work.channel.delete.hint')"
      persistent-hint
      autocomplete="off"
      autofocus
      @keyup.enter="confirm"
    />
    <!-- eslint-enable vuejs-accessibility/no-autofocus -->
  </AdaptiveDialog>
</template>

<style scoped>
.delete-channel__body {
  margin-bottom: 12px;
}
</style>
