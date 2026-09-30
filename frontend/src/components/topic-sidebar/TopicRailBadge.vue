<script setup lang="ts">
// 未读角标：一颗裸的琥珀数字，或者一个纯点（「那边有动静」，但有几条与我无关）。
//
// 这一条侧栏上有六处未读：话题行、置顶的「全局」行、「成员」行、项目头（私聊总数）、
// 「其他话题」组头、「已归档」组头。它们原来是同一段模板在六个地方各抄一遍，抄的
// 代价是「99+」这件事有六个写法。所以这里只有一条规则：给数字画数字，给 dot 画点，
// 自己不算任何东西。
import { countLabel } from '@/lib/topicTree'

defineProps<{
  /** 要显示的数字；`dot` 为真时忽略。 */
  count?: number
  /** 只画一个点。 */
  dot?: boolean
  title?: string
}>()
</script>

<template>
  <span class="unread-badge" :class="{ 'unread-badge--dot': dot }" :title="title">
    <template v-if="!dot">{{ countLabel(count ?? 0) }}</template>
  </span>
</template>

<style scoped>
/* 未读角标 (Feishu-style): 未读计数 = 裸的琥珀数字（owner 定的醒目色），行里唯一
   常驻的右对齐元素。形态历经红圆/石墨药丸被否——干净的行 + 一个琥珀数字才是答案。
   行高和字号跟着行的字号走，所以这里是 13/700 + tabular-nums：两个数字并排时
   位数不跳。 */
.unread-badge {
  flex: none;
  pointer-events: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  color: var(--accent);
  margin-left: 6px;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
/* 收起来的一组话题的组头：只给一个点，不给数字——别人话题里有几条与我无关。 */
.unread-badge--dot {
  width: 6px;
  height: 6px;
  padding: 0;
  border-radius: 50%;
  background: var(--muted);
}
</style>
