<script setup lang="ts">
// 管理台表格的旧名字。表格壳已经升格到 `components/base/BaseTable.vue`，这里只是留给
// 已有调用处的别名：属性、事件、插槽原样转交。新代码直接用 BaseTable。
import BaseTable from '@/components/base/BaseTable.vue'

defineOptions({ name: 'AdminGrid', inheritAttrs: false })
</script>

<template>
  <BaseTable v-bind="$attrs">
    <template v-for="(_, name) in $slots" #[name]="scope">
      <slot :name="name" v-bind="scope ?? {}" />
    </template>
  </BaseTable>
</template>
