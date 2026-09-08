<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const backTo = computed(() =>
  typeof route.meta.backTo === 'string' ? router.resolve({ name: route.meta.backTo }, route).path : null
)
</script>

<template>
  <!-- `:active="false"` 不是样式偏好，是修一个 bug：这颗按钮指向的是**父**地址，
       而 vue-router 的非精确匹配认为「站在子路由上时父链接是激活的」，于是
       Vuetify 一直给它盖一层 12% 的实底遮罩——一颗永远处于按下态的返回键，在
       顶栏左上角就是一个突兀的灰方块。返回是「离开这一层」，不是「你在这儿」，
       它本来就不该有激活态。 -->
  <v-btn
    v-if="backTo"
    :to="backTo"
    :active="false"
    icon
    color="on-surface-variant"
    variant="text"
    :size="mdAndUp ? 28 : 44"
    aria-label="返回上一级"
    title="返回上一级"
  >
    <v-icon size="20">mdi-arrow-left</v-icon>
  </v-btn>
</template>
