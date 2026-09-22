/**
 * 一门课的侧栏：谁能看到哪几格。
 *
 * **为什么课程要自己算一份。** `lib/shell.ts` 那条壳的链解析的是**项目**的
 * 声明（`Project.shell` → 单题 → 项目集），而课程页不是项目 —— 它们是题目板
 * 自己的管理界面，读不到壳。所以一门课的侧栏在这里算，判据只有一条：这块板的
 * 默认分组声明的壳是课程壳（服务端算好，随 `Space.isCourse` 下来，见
 * `backend/app/domain/shell/catalog.py` 的 `is_course_shell`）。
 *
 * **教师 = 本版管理员**（#1450 定的）：`space.admins` 里那个人就是「谁在教这门
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
}

/**
 * 老师（本版管理员）看到的格：这门课现在有什么在等我。
 *
 * 「设置」不在这里 —— 它不是一个页面，是那一整块现成的管理格（审核 / 模板 /
 * 分析 / 分类 / 邀请码），`SpaceSidebar` 照原样在它下面画。小测也不在这里：
 * 学生是从**这一周**进去答的（那一屏属于教学单元），不是一个常驻的格子。
 */
export const COURSE_TEACHER_CELLS: readonly CourseNavCell[] = [
  { route: 'SpacesCourseHome', icon: 'mdi-view-dashboard-outline', label: 'spaces.course.nav.overview' },
  { route: 'SpacesCourseUnits', icon: 'mdi-calendar-week-outline', label: 'spaces.course.nav.units' },
  { route: 'SpacesCourseAssignments', icon: 'mdi-clipboard-check-outline', label: 'spaces.course.nav.assignments' },
  { route: 'SpacesCoursePeople', icon: 'mdi-account-group-outline', label: 'spaces.course.nav.people' },
  {
    route: 'SpacesDetailAnalyticsLearning',
    icon: 'mdi-comment-question-outline',
    label: 'spaces.course.nav.stuck',
  },
]

/** 学生看到的格：我这门课要做什么。没有题目、没有报名、没有算力。 */
export const COURSE_STUDENT_CELLS: readonly CourseNavCell[] = [
  { route: 'SpacesCourseHome', icon: 'mdi-school-outline', label: 'spaces.course.nav.myCourse' },
  { route: 'SpacesCourseAssignments', icon: 'mdi-clipboard-text-outline', label: 'spaces.course.nav.thisWeek' },
  { route: 'SpacesCourseTeam', icon: 'mdi-account-multiple-outline', label: 'spaces.course.nav.team' },
]

export function courseCells(isTeacher: boolean): readonly CourseNavCell[] {
  return isTeacher ? COURSE_TEACHER_CELLS : COURSE_STUDENT_CELLS
}

/**
 * 「进去」一块题目板该落到哪：课 → 课程首页，其余 → 题目列表（今天的第一屏）。
 *
 * 落点必须在**点进去的那一刻**定，因为 `/spaces/{id}` 这个地址本身不区分两者 ——
 * 它一律 redirect 到题目列表（老题目板的零感知就是靠这条不许动）。所以课程那一路
 * 由入口链接显式带到 `SpacesCourseHome`：列表页、我的工作页都走这里。
 *
 * 顺带一个已知的边界：直接在地址栏敲 `/spaces/{id}`（或刷新一个这样的地址）会落
 * 到题目列表，课也一样 —— 没有请求就答不出「是不是课」，而为了答它把每次进板都
 * 拖一个往返，代价落在所有人身上。课里的侧栏第一格就是课程总览，回去只要一击。
 */
export function spaceEntryRoute(space: { id: number; isCourse?: boolean }): {
  name: string
  params: { spaceId: number }
} {
  return {
    name: space.isCourse ? 'SpacesCourseHome' : 'SpacesDetailTasksList',
    params: { spaceId: space.id },
  }
}

/** 这块板对这个人是不是「老师视角」。名单来自服务端，别在前端另判一次权限。 */
export function isCourseTeacher(adminUserIds: readonly number[], userId: number | undefined): boolean {
  return userId !== undefined && adminUserIds.includes(userId)
}
