<script setup lang="ts">
/**
 * 后台的「棘轮」（`/admin/ratchet`）：架构还债进度，按方面分区。
 *
 * 这一层是**容器**：取数、拉取、把这次拉取的结果留在屏幕上；画面在同目录的
 * `AdminRatchetPageView.vue` 里（`pnpm run lint:scenes` 把它当容器看，冻结的是那个视图；
 * 配对视图的名字是这一条规则机械算出来的——页面文件名加 `View.vue`，所以不能随便叫）。
 * 分开还有一层原因：视图是 A 档场景，给它一组 props 就能单独挂起来看，取数的能力
 * 留在这一层，两边都各归各位。
 *
 * 这一页是**只读的**：它不定义任何一条判定，只把采集脚本每道检查自己吐出来的数
 * 画出来。规则在各自的检查器里（`.claude/scripts/ratchet-snapshot.py` 是那份清单），
 * 采集在 `.github/workflows/arch-metrics.yml` 里跑，后端把每次采集的工件存下来
 * （`POST /admin/ratchet/refresh` 是唯一会去 GitHub 的动作），这里读存档。所以这一页
 * 上不可能出现一个「检查器没量过」的数。
 */
import type { RatchetBoard } from '@/views/admin/ratchetApi'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminRatchetPageView from '@/views/admin/AdminRatchetPageView.vue'
import { getRatchetBoard, refreshRatchetBoard } from '@/views/admin/ratchetApi'

defineOptions({ name: 'AdminRatchetPage' })

const { t } = useI18n()

const board = ref<RatchetBoard | null>(null)
const loading = ref(true)
/** 「这个归档读不出来」。它只表示**手上没有 board 可画**——拿到 board 的那一刻就
 *  该清掉，否则一次成功的刷新会被上一屏的错继续盖着（视图那一支因此还要 `!board`，
 *  两道都挡住同一件事）。 */
const failed = ref(false)
/** 拉取的结果（成功或失败）留在页面上：一次拉取改的是存档，不是这一页的样子，
 *  说清楚「这次拉到了什么」比把提示一闪而过重要。 */
const pull = ref('')
const pullFailed = ref(false)
const refreshing = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    board.value = await getRatchetBoard()
  } catch {
    // 读失败**不是**「还没有采集」：两句话，两个画面。
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  pullFailed.value = false
  pull.value = ''
  try {
    const next = await refreshRatchetBoard()
    board.value = next
    // 拉到东西了，就不再有「读不出来」这回事——哪怕上一次 GET 是失败的。
    // 拉取自己的成败另说，在下面按 `report.error` 报告：那是这一屏的一次拉取
    // 结果，不是「没有存档」。
    failed.value = false
    const report = next.refresh
    if (!report) return
    if (report.error) {
      pullFailed.value = true
      pull.value = t('ratchet.pull.failed', { error: report.error })
      return
    }
    pull.value = t('ratchet.pull.done', {
      listed: report.listed,
      stored: report.stored,
      already: report.already_stored,
    })
  } catch {
    pullFailed.value = true
    pull.value = t('ratchet.pull.unreachable')
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <AdminRatchetPageView
    :board="board"
    :loading="loading"
    :failed="failed"
    :pull="pull"
    :pull-failed="pullFailed"
    :refreshing="refreshing"
    @refresh="refresh"
    @retry="load"
  />
</template>
