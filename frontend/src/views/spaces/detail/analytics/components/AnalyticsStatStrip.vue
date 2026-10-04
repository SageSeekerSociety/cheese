<template>
  <!-- 一条指标：上下两条细线，格与格之间一条竖线，不套卡片。 -->
  <div class="strip"><slot /></div>
</template>

<style scoped>
.strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.strip > :slotted(:not(:first-child)) {
  border-left: 1px solid var(--line);
}
</style>
