<template>
  <answer-list :question-id="questionId" />
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import AnswerList from '@/components/questions/AnswerList.vue'

const nav = useNavigation()

const questionId = computed(() => Number(nav?.route?.params?.questionId))
</script>
