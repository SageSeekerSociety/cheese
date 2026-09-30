<template>
  <div class="categories-and-topics">
    <ManageCategories />
    <ManageTopics />
  </div>
</template>

<script setup lang="ts">
import ManageCategories from '../ManageCategories.vue'
import ManageTopics from '../ManageTopics.vue'
</script>

<style scoped>
.categories-and-topics {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
</style>
