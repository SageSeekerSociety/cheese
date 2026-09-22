import { describe, expect, it } from 'vitest'

import { COURSE_STUDENT_CELLS, COURSE_TEACHER_CELLS, courseCells, isCourseTeacher, spaceEntryRoute } from './courseNav'

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

  it('sends a course to the course home and a board to the problem list', () => {
    expect(spaceEntryRoute({ id: 7, isCourse: true })).toEqual({
      name: 'SpacesCourseHome',
      params: { spaceId: 7 },
    })
    expect(spaceEntryRoute({ id: 7, isCourse: false })).toEqual({
      name: 'SpacesDetail',
      params: { spaceId: 7 },
    })
    // 老题目板：服务端不说，就是没有壳 → 还是今天那条地址（它自己 redirect 到题目
    // 列表），所以链接一个字符都没变。
    expect(spaceEntryRoute({ id: 7 })).toEqual({
      name: 'SpacesDetail',
      params: { spaceId: 7 },
    })
  })
})
