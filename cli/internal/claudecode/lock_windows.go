package claudecode

import (
	"os"

	"golang.org/x/sys/windows"
)

// lockedByte is the byte the executor's bootstrap locks with msvcrt.locking
// (`remote_execution/executor_download.lock`): on Windows two locks exclude
// each other only over the same bytes, so this takes the same one.
const lockedByte = 0x7FFFFFF0

// lockFile holds an exclusive lock on path until the returned function is called.
func lockFile(path string) (func(), error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	overlapped := windows.Overlapped{Offset: lockedByte}
	if err := windows.LockFileEx(windows.Handle(file.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0, &overlapped); err != nil {
		file.Close()
		return nil, err
	}
	return func() { file.Close() }, nil
}
