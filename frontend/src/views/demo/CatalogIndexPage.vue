<script setup lang="ts">
// 目录页：每个组件一张卡，卡上一小格活预览（CatalogThumb.vue 负责只在看得见的时候挂）。
//
// 卡上写的是「它是什么、在哪儿、独立渲染要哪几样」——找组件的人先要知道的就是这三样。
import type { CatalogGroup } from './demoCatalogStudio'

import CatalogThumb from './CatalogThumb.vue'
import { needsOf } from './demoCatalogStudio'

defineProps<{
  groups: CatalogGroup[]
  /** 搜索那一句：读屏软件听得到「找到几个」。 */
  note: string
}>()
</script>

<template>
  <div class="index">
    <h1 class="catalog-name t-page-title">组件目录</h1>
    <p class="catalog-lede">
      每个组件一页，用真产品的形状渲染几种状态。这一页不连后端、不登录 —— <code>pnpm dev</code> 打开
      <code>/demo/catalog</code> 就能看。加一个组件：<code>node scripts/catalog-scaffold.mjs</code> 按 props
      生成骨架，补完后放进 <code>catalog.ts</code> 或它展开的分册。
    </p>
    <!-- 一直在页面上的那一格：内容变了读屏软件才会念（中途插进来的 live region 常常不念）。 -->
    <p class="catalog-result" role="status">{{ note }}</p>
    <section v-for="group in groups" :key="group.name" class="catalog-group">
      <h2 class="catalog-group-name">
        <code>{{ group.name }}</code> <span>{{ group.items.length }}</span>
      </h2>
      <ul class="catalog-index">
        <li v-for="item in group.items" :key="item.id" class="catalog-cell">
          <CatalogThumb :entry="item" />
          <a class="catalog-card" :href="`/demo/catalog/${item.id}`">
            <span class="catalog-card-title">{{ item.title }}</span>
            <span class="catalog-card-about">{{ item.about }}</span>
            <span class="catalog-card-meta">
              <code>{{ item.file }}</code>
              <span>{{ needsOf(item.needs) }}</span>
              <span>{{ item.states.length }} 格</span>
            </span>
          </a>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.index {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px 32px;
}

/* 字号和字重走 `.t-page-title`（23px 那一档，设计系统里的页面标题）：手写 20px 不在
   12/13/14/15/18/23 这条阶梯上，而这一页的标题就是全站的页面标题。 */
.catalog-name {
  margin: 0 0 4px;
}

.catalog-lede {
  max-width: 60ch;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-lede code,
.catalog-card-meta code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.catalog-result {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}

/* 没在搜的时候它是空的：不占那一行，但留在无障碍树里（display: none 会让它下次不被念）。 */
.catalog-result:empty {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}

.catalog-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.catalog-group-name {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.catalog-group-name code {
  font-family: var(--font-mono);
}

.catalog-index {
  display: grid;
  gap: 8px;

  /* 窄屏（360 宽的手机减去左右留白）放不下 320px 一列：最窄退到整行宽。 */
  grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
  padding: 0;
  margin: 0;
  list-style: none;
}

/* 框给这一格：预览和文字在同一个盒子里，位置也要留给铺满整格的链接。 */
.catalog-cell {
  display: flex;
  position: relative;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  flex-direction: column;
}

.catalog-cell:hover {
  border-color: var(--accent);
}

.catalog-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px 12px;
  color: inherit;
  text-decoration: none;
}

/* 整张卡都能点：链接铺满这一格。链接本身只包着文字，所以卡上的预览（里面是活组件）
   不在链接里面，不用挤进一条链接的内容模型里。 */
.catalog-card::after {
  position: absolute;
  inset: 0;
  content: '';
}

.catalog-card:focus-visible::after {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.catalog-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.catalog-card-about {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);

  /* 文件路径是最长的那一格，而 flex 子项的 min-content 就是「整条路径」的宽度：
     不折断的话它会把卡片撑破（深路径那几个正好卡在断行还没到的地方）。 */
  overflow-wrap: anywhere;
}
</style>
