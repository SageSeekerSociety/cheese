<template>
  <div
    ref="scrollContainer"
    :class="['infinite-scroll-container', { 'scroll-enabled': scrollEnabled }]"
    @scroll="handleScroll"
  >
    <slot v-if="initialLoading" name="skeleton">
      <div class="skeleton d-flex justify-center align-center flex-column">
        <v-progress-circular indeterminate size="48" />
        <span class="text-subtitle-1 mt-2">{{ t('shell.list.loading') }}</span>
      </div>
    </slot>
    <template v-else>
      <slot></slot>
      <slot v-if="isEmpty" name="empty">
        <div class="empty d-flex justify-center align-center flex-row">
          <v-icon>mdi-alert-circle</v-icon>
          <span class="text-caption ml-2">{{ t('shell.list.empty') }}</span>
        </div>
      </slot>
      <slot v-if="loading" name="loading">
        <div class="loading d-flex justify-center align-center flex-row">
          <v-progress-circular indeterminate size="16" width="2" />
          <span class="text-caption ml-2">{{ t('shell.list.loading') }}</span>
        </div>
      </slot>
      <slot v-if="manualMode && hasMore && !loading" name="manual-load">
        <div class="manual-load-button">
          <BaseButton kind="secondary" size="sm" @click="manualLoadMore">{{ t('shell.list.loadMore') }}</BaseButton>
          <!-- Same counter as the feedback list: how many are in hand out of the server's total. Hidden when the API reports no total. -->
          <span v-if="total > 0" class="t-meta-read t-num">{{ t('shell.list.showing', { shown, total }) }}</span>
        </div>
      </slot>
      <slot v-if="!hasMore && !isEmpty" name="no-more">
        <div class="no-more d-flex justify-center align-center flex-row text-medium-emphasis gap-1">
          <v-icon size="small">mdi-alert-circle-outline</v-icon>
          <span class="text-caption">{{ t('shell.list.end') }}</span>
        </div>
      </slot>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

interface Props {
  loading: boolean
  hasMore: boolean
  initialLoading?: boolean
  isEmpty?: boolean
  threshold?: number
  scrollEnabled?: boolean
  forceManual?: boolean
  /** 手上几条；配合 total 画「已显示 X / 共 Y 条」。 */
  shown?: number
  /** 服务端一共几条；接口不给就是 0，这时不画计数。 */
  total?: number
}

const props = withDefaults(defineProps<Props>(), {
  threshold: 100,
  scrollEnabled: false,
  forceManual: false,
  initialLoading: false,
  isEmpty: false,
  shown: 0,
  total: 0,
})

const emit = defineEmits<{
  (e: 'load-more'): void
}>()

const scrollContainer = ref<HTMLElement | null>(null)
const isContentFull = ref(false)
const manualMode = computed(() => props.forceManual || !isContentFull.value)

const handleScroll = () => {
  if (manualMode.value) return

  const container = props.scrollEnabled ? scrollContainer.value : window
  if (!container) return

  let scrollTop, scrollHeight, clientHeight

  if (props.scrollEnabled && scrollContainer.value) {
    scrollTop = scrollContainer.value.scrollTop
    scrollHeight = scrollContainer.value.scrollHeight
    clientHeight = scrollContainer.value.clientHeight
  } else {
    scrollTop = window.pageYOffset
    scrollHeight = document.documentElement.scrollHeight
    clientHeight = window.innerHeight
  }

  if (scrollHeight - scrollTop - clientHeight < props.threshold && !props.loading && props.hasMore) {
    emit('load-more')
  }
}

const checkContentFull = () => {
  if (!props.scrollEnabled) {
    const scrollHeight = document.documentElement.scrollHeight
    const clientHeight = window.innerHeight
    isContentFull.value = scrollHeight > clientHeight
  } else if (scrollContainer.value) {
    isContentFull.value = scrollContainer.value.scrollHeight > scrollContainer.value.clientHeight
  }
}

const checkAndLoadMore = () => {
  checkContentFull()
  if (!isContentFull.value && !props.loading && props.hasMore && !props.forceManual) {
    emit('load-more')
  }
}

const manualLoadMore = () => {
  emit('load-more')
}

onMounted(() => {
  const container = props.scrollEnabled ? scrollContainer.value : window
  if (container) {
    container.addEventListener('scroll', handleScroll)
  }
  nextTick(() => {
    checkAndLoadMore()
  })
})

onUnmounted(() => {
  const container = props.scrollEnabled ? scrollContainer.value : window
  if (container) {
    container.removeEventListener('scroll', handleScroll)
  }
})

watch(
  () => props.hasMore,
  (newValue) => {
    if (newValue) {
      nextTick(() => {
        checkAndLoadMore()
      })
    }
  }
)

watch(
  () => props.scrollEnabled,
  (newValue, oldValue) => {
    if (newValue !== oldValue) {
      const oldContainer = oldValue ? scrollContainer.value : window
      const newContainer = newValue ? scrollContainer.value : window

      if (oldContainer) {
        oldContainer.removeEventListener('scroll', handleScroll)
      }
      if (newContainer) {
        newContainer.addEventListener('scroll', handleScroll)
      }
    }
  }
)

watch(
  () => props.loading,
  () => {
    nextTick(() => {
      checkContentFull()
    })
  }
)
</script>

<style scoped>
.infinite-scroll-container {
  height: 100%;
}

.infinite-scroll-container.scroll-enabled {
  overflow-y: auto;
}

.loading,
.no-more,
.manual-load-button,
.skeleton,
.empty {
  text-align: center;
  padding: 10px 0;
}

.manual-load-button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  width: 100%;
  background-color: var(--fill);
  border: none;
  cursor: pointer;
}
</style>
