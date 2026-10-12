<script setup lang="ts">
// 常驻的左栏：按源码目录分组的整份目录，每一页都在。
//
// 分组就是这个仓库自己给组件分的类（`catalogGroup()`），不另起一套。几百条平铺成
// 一列没人找得到，所以按目录收起来：当前这一条在哪一组，那一组默认展开。
import type { CatalogGroup } from './demoCatalogStudio'

import { ref, watch } from 'vue'

const props = defineProps<{
  groups: CatalogGroup[]
  /** 现在站在哪一条上（单组件页才有）。 */
  current?: string | null
}>()

/** 展开的是哪几组。 */
const open = ref<string[]>([])

function sync(current: string | null | undefined): void {
  open.value = props.groups.filter((group) => group.items.some((item) => item.id === current)).map((g) => g.name)
}

watch(() => props.current, sync, { immediate: true })

function toggle(name: string): void {
  open.value = open.value.includes(name) ? open.value.filter((n) => n !== name) : [...open.value, name]
}
</script>

<template>
  <nav class="rail" aria-label="组件目录">
    <h2 class="rail-title">组件（按源码目录）</h2>
    <ul class="rail-groups">
      <li v-for="group in groups" :key="group.name" class="rail-group">
        <button type="button" class="rail-head" :aria-expanded="open.includes(group.name)" @click="toggle(group.name)">
          <span class="rail-caret" aria-hidden="true" />
          <code class="rail-name">{{ group.name }}</code>
          <span class="rail-count">{{ group.items.length }}</span>
        </button>
        <!-- 收起来的那几组不在 DOM 里：v-show 也会把这一列全部 273 条建出来。 -->
        <ul v-if="open.includes(group.name)" class="rail-items">
          <li v-for="item in group.items" :key="item.id">
            <a
              class="rail-link"
              :href="`/demo/catalog/${item.id}`"
              :aria-current="item.id === current ? 'page' : undefined"
              :title="item.about"
              >{{ item.title }}</a
            >
          </li>
        </ul>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.rail {
  padding: 12px 8px 24px;
}

.rail-title {
  padding: 0 8px;
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--faint);
}

.rail-groups,
.rail-items {
  padding: 0;
  margin: 0;
  list-style: none;
}

.rail-head {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 100%;
  padding: 5px 8px;
  font: inherit;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.rail-head:hover {
  color: var(--ink);
  background: var(--fill);
}

/* 收起来是个三角，展开转 90 度。 */
.rail-caret {
  width: 0;
  height: 0;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
  border-left: 5px solid currentcolor;
  transition: transform var(--dur-quick) var(--ease-out);
}

.rail-head[aria-expanded='true'] .rail-caret {
  transform: rotate(90deg);
}

.rail-name {
  font-family: var(--font-mono);
  font-size: 12px;
  word-break: break-all;
}

.rail-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--faint);
}

.rail-link {
  display: block;
  padding: 5px 8px 5px 22px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-decoration: none;
  border-radius: var(--radius-sm);
}

.rail-link:hover {
  background: var(--fill);
}

.rail-link[aria-current='page'] {
  color: var(--accent-ink);
  background: var(--accent-wash);
}
</style>
