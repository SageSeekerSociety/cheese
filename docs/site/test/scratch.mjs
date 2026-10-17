// A build directory a test makes for itself, removed when the process exits
// (process.exit included). A directory handed in through OUT is never touched.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export function scratchDir(prefix) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix))
  process.on('exit', () => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}
