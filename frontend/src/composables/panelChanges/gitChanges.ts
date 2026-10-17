// 「改动」那一格的工作树差异：这份 diff 本身和由它切出来的每个文件一段。
//
// 提交记录不在这里读：审阅看的是改了什么，提交是过程记录。
import type { Ref } from 'vue'
import type { FileSource } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'
import type { PanelChangesProps } from './types'

import { computed, ref } from 'vue'

import { getGitDiff } from '../../api'
import { splitDiffByFile } from '../../lib/diff'

import { t } from '@/i18n'

export interface GitChangesDeps {
  props: PanelChangesProps
  /** 这一格看的那件任务。 */
  task: Ref<string | null>
  /** 打开的文件看哪一面：交上来的那一版，还是现场的工作区。 */
  source: Ref<FileSource>
  sourceUnavailable: Ref<boolean>
  noRepo: Ref<boolean>
  noWorkYet: Ref<boolean>
  /** A background re-fetch: spins only the 刷新 button, never replaces the panel. */
  refreshing: Ref<boolean>
  errorMsg: Ref<string | null>
  /** 这一格现在这一代；路上那一次回来时对不上就丢掉。 */
  epoch: () => number
  /** 开始一次前台读取：转圈要等最后一个回来的。 */
  startLoad: () => () => void
}

/** 这一格的 diff 取数：读什么、什么时候读不读，都由递进来的那几个判据决定。 */
export function usePanelGitChanges(deps: GitChangesDeps) {
  const {
    props,
    task: selectedTask,
    source: fileSource,
    sourceUnavailable,
    noRepo,
    noWorkYet,
    refreshing,
    errorMsg,
    epoch,
    startLoad,
  } = deps

  // ---- Git: the working-tree diff ----
  const gitDiff = ref<string>('')

  async function loadGit(opts: { silent?: boolean } = {}) {
    const tid = props.topicId
    const task = selectedTask.value
    const pid = props.projectId
    const epochAt = epoch()
    if (!tid || !pid || !task || sourceUnavailable.value || noRepo.value || noWorkYet.value) return
    const done = opts.silent ? null : startLoad()
    if (opts.silent) refreshing.value = true
    errorMsg.value = null
    try {
      const diff = await getGitDiff(pid, tid, task, fileSource.value)
      // Guard against a source switch mid-flight.
      if (selectedTask.value !== task || epoch() !== epochAt) return
      gitDiff.value = diff.diff
    } catch (e) {
      // 后台那一下没取到，就留着上次取到的：下一次再取，不拿一句报错把整块顶掉。
      if (epoch() === epochAt && !opts.silent) {
        errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.loadFailed')
      }
    } finally {
      done?.()
      if (selectedTask.value === task && epoch() === epochAt) refreshing.value = false
    }
  }

  // 一份 diff，切成每个文件一段。树上的 +N −M、点开文件看的那一段，都读这里 ——
  // 不再多要一次请求，也不会出现「树说改了、diff 里没有」这种两边不一致。
  const fileDiffs = computed<FileDiff[]>(() => splitDiffByFile(gitDiff.value))
  const diffByPath = computed(() => new Map(fileDiffs.value.map((f) => [f.path, f])))

  /** 换了来源或任务：上一代的 diff 不能再留着。 */
  function clearGitDiff() {
    gitDiff.value = ''
  }

  return { gitDiff, loadGit, fileDiffs, diffByPath, clearGitDiff }
}
