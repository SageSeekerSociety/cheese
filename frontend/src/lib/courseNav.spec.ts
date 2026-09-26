import { describe, expect, it } from 'vitest'

import {
  COURSE_MODULES,
  COURSE_STUDENT_CELLS,
  COURSE_TEACHER_CELLS,
  courseCells,
  isCourseTeacher,
  moduleOn,
  spaceEntryRoute,
  visibleCourseCells,
} from './courseNav'

describe('courseNav', () => {
  it('gives a teacher the course cells and a student the student cells', () => {
    expect(courseCells(true)).toBe(COURSE_TEACHER_CELLS)
    expect(courseCells(false)).toBe(COURSE_STUDENT_CELLS)
  })

  it('keeps the teacher-only cells out of the student sidebar', () => {
    // 哪个学生都不该在侧栏看到「学生与分组」「共性问题」这些格：露出来就是把人送
    // 到一串 403 上。教师那几格里至少这几个是学生不能有的。
    const studentRoutes = COURSE_STUDENT_CELLS.map((cell) => cell.route)
    expect(studentRoutes).not.toContain('SpacesCoursePeople')
    expect(studentRoutes).not.toContain('SpacesDetailAnalyticsLearning')
    expect(studentRoutes).not.toContain('SpacesCourseUnits')
  })

  it('starts both roles on the same first screen', () => {
    expect(COURSE_TEACHER_CELLS[0].route).toBe('SpacesCourseHome')
    expect(COURSE_STUDENT_CELLS[0].route).toBe('SpacesCourseHome')
  })

  it('names the same route twice for the two meanings of one page', () => {
    // 学生的「本周任务」和老师的「作业与验收」是同一条路由（`course/assignments`），
    // 只是两边叫法不同 —— 所以它必须同时出现在两份清单里，且 label 不同。
    const student = COURSE_STUDENT_CELLS.find((cell) => cell.route === 'SpacesCourseAssignments')
    const teacher = COURSE_TEACHER_CELLS.find((cell) => cell.route === 'SpacesCourseAssignments')
    expect(student?.label).toBe('spaces.course.nav.thisWeek')
    expect(teacher?.label).toBe('spaces.course.nav.assignments')
  })

  it('reads the teacher list the server sent, not a role of its own', () => {
    expect(isCourseTeacher([4, 9], 9)).toBe(true)
    expect(isCourseTeacher([4, 9], 5)).toBe(false)
    expect(isCourseTeacher([], 5)).toBe(false)
    expect(isCourseTeacher([5], undefined)).toBe(false)
  })

  it('sends every board to the board itself, courses included', () => {
    // 新建的题目板**就是一门课**（#1448），所以按 `isCourse` 分岔等于「新板全开在
    // 老树上」—— 落点不再看它。课那几屏由题目板外壳里那格「课程」接住。
    expect(spaceEntryRoute({ id: 7 })).toEqual({
      name: 'SpaceBoardHome',
      params: { spaceId: 7 },
    })
  })
})

describe('course modules', () => {
  it('means "on" when nothing was declared', () => {
    // `{}` = 一间全都露出来的课；老题目板连这个字段都没有，落在同一档。
    expect(moduleOn({}, 'quiz')).toBe(true)
    expect(moduleOn(undefined, 'quiz')).toBe(true)
  })

  it('means "off" only for the module that said so', () => {
    expect(moduleOn({ quiz: false }, 'quiz')).toBe(false)
    expect(moduleOn({ quiz: false }, 'team')).toBe(true)
  })

  it('drops the cells of a module that is off, and keeps the ones that are not', () => {
    const teacher = visibleCourseCells(COURSE_TEACHER_CELLS, { units: false, stuck: false })
    const routes = teacher.map((cell) => cell.route)
    expect(routes).not.toContain('SpacesCourseUnits')
    expect(routes).not.toContain('SpacesDetailAnalyticsLearning')
    expect(routes).toContain('SpacesCourseAssignments')

    const student = visibleCourseCells(COURSE_STUDENT_CELLS, { team: false })
    expect(student.map((cell) => cell.route)).not.toContain('SpacesCourseTeam')
  })

  it('never drops the course home, whatever the switches say', () => {
    // 课程总览是这门课本身，不归任何开关管 —— 关光所有模块也不该出现一间没有
    // 首页的课。
    const everythingOff = Object.fromEntries(COURSE_MODULES.map((mod) => [mod.key, false]))
    expect(visibleCourseCells(COURSE_TEACHER_CELLS, everythingOff).map((cell) => cell.route)).toEqual([
      'SpacesCourseHome',
      'SpacesCoursePeople',
    ])
  })

  it('changes nothing when nothing is declared', () => {
    expect(visibleCourseCells(COURSE_TEACHER_CELLS, {})).toEqual(COURSE_TEACHER_CELLS)
    expect(visibleCourseCells(COURSE_STUDENT_CELLS, undefined)).toEqual(COURSE_STUDENT_CELLS)
  })

  it('only marks cells of modules the catalogue knows', () => {
    // 格子上的 module 必须是键表里的键：写错一个字母的后果是那一格永远不出现
    // （或者永远关不掉），而这两种都很难在界面上看出来。
    const keys = new Set(COURSE_MODULES.map((mod) => mod.key))
    for (const cell of [...COURSE_TEACHER_CELLS, ...COURSE_STUDENT_CELLS]) {
      if (cell.module) expect(keys.has(cell.module)).toBe(true)
    }
  })

  it('is honest about which modules have a screen today', () => {
    // 拨了没反应的开关比没有开关更糟：配置页只画 wired 的那几格，其余的在页面
    // 下方一句交代。这条钉住「有界面的那几格确实是接线的那几格」。
    const wired = COURSE_MODULES.filter((mod) => mod.wired).map((mod) => mod.key)
    expect(wired).toEqual(['units', 'assignments', 'team', 'stuck'])
  })
})
