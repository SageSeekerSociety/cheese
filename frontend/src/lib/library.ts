// 资料库里那一份的地址：`library/<名字>`。
//
// 用户给这个项目的文件，项目级、按原名寻址、只读。消息里的附件、字节端点的 query、
// 芝士工作目录里的那一份，用的都是这一个地址——所以「这个路径指的是资料库里的原件」
// 这件事只能有一个答案，写在这里，而不是在每个要判断它的组件里各写一次。
export const LIBRARY_PREFIX = 'library/'

export function isLibraryPath(path: string): boolean {
  return path.startsWith(LIBRARY_PREFIX)
}
