/**
 * 一门课的侧栏：谁能看到哪几格。
 *
 * **为什么课程要自己算一份。** `lib/shell.ts` 那条壳的链解析的是**项目**的
 * 声明（`Project.shell` → 单题 → 项目集），而课程页不是项目 —— 它们是题目板
 * 自己的管理界面，读不到壳。所以一门课的侧栏在这里算，判据只有一条：这块板的
 * 默认分组声明的壳是课程壳（服务端算好，随 `Space.isCourse` 下来，见
 * `backend/app/domain/shell/catalog.py` 的 `is_course_shell`）。
 *
 * **「谁在教这门课」= 本版管理员**（#1450 定的）：`space.admins` 里那个人是那个
 * 课」，名单是后端从 `space_admin_relation` 发下来的。这里不新造角色字段，也不
 * 自己再判一次权限 —— 屏上露哪几格跟着这份名单走，接口那一侧另有它自己的守卫。
 *
 * 格子用的是路由名，路由在 `router/spaces.ts` 里一次加齐；后面的任务只填组件，
 * 不再动路由与这里。
 */
export interface CourseNavCell {
  /** 路由名，必须在 `router/spaces.ts` 里存在。 */
  route: string
  icon: string
  /** i18n key，前缀 `spaces.course.nav.`。 */
  label: string
  /**
   * 这一格属于哪个模块（`COURSE_MODULES` 的键）。**不写 = 永远露出来** —— 课程
   * 总览那样的格子是这门课本身，不归任何开关管。
   */
  module?: string
}

/**
 * 管理员看到的格：这门课现在有什么在等我。
 *
 * 「设置」不在这里 —— 它不是一个页面，是那一整块现成的管理格（审核 / 模板 /
 * 分析 / 分类 / 邀请码），`SpaceSidebar` 照原样在它下面画。小测也不在这里：
 * 成员是从**这一周**进去答的（那一屏属于教学单元），不是一个常驻的格子。
 */
export const COURSE_TEACHER_CELLS: readonly CourseNavCell[] = [
  { route: 'SpacesCourseHome', icon: 'mdi-view-dashboard-outline', label: 'spaces.course.nav.overview' },
  {
    route: 'SpacesCourseUnits',
    icon: 'mdi-calendar-week-outline',
    label: 'spaces.course.nav.units',
    module: 'units',
  },
  {
    route: 'SpacesCourseAssignments',
    icon: 'mdi-clipboard-check-outline',
    label: 'spaces.course.nav.assignments',
    module: 'assignments',
  },
  { route: 'SpacesCoursePeople', icon: 'mdi-account-group-outline', label: 'spaces.course.nav.people' },
  {
    route: 'SpacesDetailAnalyticsLearning',
    icon: 'mdi-comment-question-outline',
    label: 'spaces.course.nav.stuck',
    module: 'stuck',
  },
]

/** 成员看到的格：我这门课要做什么。没有题目、没有报名、没有算力。 */
export const COURSE_STUDENT_CELLS: readonly CourseNavCell[] = [
  { route: 'SpacesCourseHome', icon: 'mdi-school-outline', label: 'spaces.course.nav.myCourse' },
  {
    route: 'SpacesCourseAssignments',
    icon: 'mdi-clipboard-text-outline',
    label: 'spaces.course.nav.thisWeek',
    module: 'assignments',
  },
  {
    route: 'SpacesCourseTeam',
    icon: 'mdi-account-multiple-outline',
    label: 'spaces.course.nav.team',
    module: 'team',
  },
]

/**
 * 这门课可以拨的模块。键与服务端 `backend/app/domain/space/course_modules.py` 的
 * `MODULE_KEYS` 是同一张表（服务端验键、这里给名字）。
 *
 * **`wired` 说的是「今天有没有界面在听这个开关」。** 拨了没反应的开关比没有开关
 * 更糟，所以配置页只画 `wired: true` 的那几格，其余的在页面下方一句话交代：接上
 * 界面之后它们自己会出现（数据早就存得住，缺的只是那个界面）。
 */
export const COURSE_MODULES: readonly {
  key: string
  label: string
  /** 这一格开/关时界面上会多/少什么，用一句人话写在配置页上。 */
  effect: string
  wired: boolean
}[] = [
  { key: 'units', label: 'spaces.course.module.units', effect: 'spaces.course.module.unitsEffect', wired: true },
  {
    key: 'assignments',
    label: 'spaces.course.module.assignments',
    effect: 'spaces.course.module.assignmentsEffect',
    wired: true,
  },
  { key: 'team', label: 'spaces.course.module.team', effect: 'spaces.course.module.teamEffect', wired: true },
  { key: 'stuck', label: 'spaces.course.module.stuck', effect: 'spaces.course.module.stuckEffect', wired: true },
  { key: 'quiz', label: 'spaces.course.module.quiz', effect: '', wired: false },
  { key: 'materials', label: 'spaces.course.module.materials', effect: '', wired: false },
  { key: 'progress', label: 'spaces.course.module.progress', effect: '', wired: false },
  { key: 'pool', label: 'spaces.course.module.pool', effect: '', wired: false },
]

/**
 * 一个模块开着吗。**缺省是开着** —— 没声明的键 = 显示，所以 `{}` 是一间全都露出来
 * 的课，老题目板（连这个字段都没有）也落在同一档。这与服务端
 * `course_modules.is_on` 是同一条规矩。
 */
export function moduleOn(modules: Record<string, boolean> | undefined, key: string): boolean {
  return modules?.[key] !== false
}

/** 按开关筛过的格子。没挂模块的格子（课程总览）永远在。 */
export function visibleCourseCells(
  cells: readonly CourseNavCell[],
  modules: Record<string, boolean> | undefined
): readonly CourseNavCell[] {
  return cells.filter((cell) => !cell.module || moduleOn(modules, cell.module))
}

export function courseCells(isTeacher: boolean): readonly CourseNavCell[] {
  return isTeacher ? COURSE_TEACHER_CELLS : COURSE_STUDENT_CELLS
}

/**
 * 「进去」一块题目板该落到哪：**题目板**（`/spaces/{id}/board`）。
 *
 * **这里不再按 `isCourse` 分岔（2026-09-26 改的）。** 从 #1448/#1454（2026-09-22）
 * 起，新建的题目板**就是一门课**（默认分组声明课程壳，`DEFAULT_CATEGORY_SHELL_NAME`），
 * 而那两版让课开在自己的课程首页上。于是按 `isCourse` 分岔的实际效果是——**新板
 * 全都开在老树上**，这块新题目板一辈子只有那些 9-22 之前建的老板子走得到。重设计的
 * 那句话是「打开空间就是新样子」，那就得真让每一次进板都落在新界面上。
 *
 * 课没有被丢掉：题目板外壳里给 `isCourse` 的板子多一格「课程」（`SpaceBoardShell.vue`），
 * 教学单元、作业、小测、小组那几屏照旧在，只是从题目板作为入口多走一步。
 *
 * 落点必须由入口链接定，因为 `/spaces/{id}` 这个地址本身不区分 —— 它一律 redirect
 * 到老树（`router/spaces.ts` 里那条 redirect 不许动，老链接的零感知靠它）。所以
 * 列表页、我的工作页、申请列表三处都走这个函数，谁也别自己拼地址。
 *
 * 一个已知的边界：直接在地址栏敲 `/spaces/{id}`（或刷新一个这样的地址）仍然落到
 * 老树 —— 那条地址是 redirect，读不到「该不该去题目板」，为它加一个往返不划算。
 */
export function spaceEntryRoute(space: { id: number }): {
  name: string
  params: { spaceId: number }
} {
  return {
    name: 'SpaceBoardHome',
    params: { spaceId: space.id },
  }
}

/** 这块板对这个人是不是「管理员视角」。名单来自服务端，别在前端另判一次权限。 */
export function isCourseTeacher(adminUserIds: readonly number[], userId: number | undefined): boolean {
  return userId !== undefined && adminUserIds.includes(userId)
}

/**
 * 一份作业在成员这一侧的状态：服务端算好的 `completionStatus`，翻成人话和颜色。
 * 「我的课程」和「本周任务」两屏用同一张表，免得同一份作业在两处叫法不同。
 */
const WORK_STATUS: Record<string, { label: string; color: string }> = {
  NOT_SUBMITTED: { label: 'spaces.course.myCourse.status.notSubmitted', color: 'warning' },
  PENDING_REVIEW: { label: 'spaces.course.myCourse.status.pendingReview', color: 'primary' },
  REJECTED_RESUBMITTABLE: { label: 'spaces.course.myCourse.status.resubmittable', color: 'primary' },
  FAILED: { label: 'spaces.course.myCourse.status.failed', color: 'primary' },
  SUCCESS: { label: 'spaces.course.myCourse.status.success', color: 'success' },
}

export function courseWorkStatus(status: string): { label: string; color: string } {
  return WORK_STATUS[status] ?? { label: 'spaces.course.myCourse.status.unknown', color: 'primary' }
}
