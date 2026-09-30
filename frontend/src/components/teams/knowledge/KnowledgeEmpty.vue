<script setup lang="ts">
// 一条资料都没有时那一块。
//
// 两句话分岔：筛过（搜索框 / 类型 / 标签里有东西）说的是「换个条件试试」，
// 没筛过说的是「先去聊天里放点东西进来」—— 这两种「空」不是同一件事，
// 说错哪一种都会让人以为是坏了。判据由页递进来（`hasFilters`）。
defineOptions({ name: 'KnowledgeEmpty' })

defineProps<{ hasFilters: boolean }>()
</script>

<template>
  <div class="empty-knowledge-state text-center py-12">
    <v-icon icon="mdi-book-open-page-variant" size="64" class="mb-4 empty-state-icon"></v-icon>
    <h3 class="text-h6 font-weight-medium mb-2">知识库暂无内容</h3>
    <p v-if="hasFilters" class="text-body-2 text-medium-emphasis mb-4">
      没有找到匹配当前筛选条件的资料，请尝试调整筛选条件
    </p>
    <p v-else class="text-body-2 text-medium-emphasis mb-6">在频道聊天中添加有价值的内容到知识库，方便团队随时查阅</p>
  </div>
</template>

<style scoped lang="scss">
.empty-knowledge-state {
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-lg);
  background-color: var(--fill);
}

/* 空状态插图：元信息级别的装饰，两个主题下都该"看得见但不抢戏"。
   --line-2 浅色是 #E2E3E6（和原来的 grey-lighten-2 #E0E0E0 几乎同值），
   深色是 #3A3E45（在 #141517 的页面上仍是一块看得出的形状）。 */
.empty-state-icon {
  color: var(--line-2);
}
</style>
