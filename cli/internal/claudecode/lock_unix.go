//go:build !windows

package claudecode

import (
	"os"
	"syscall"
)

// lockFile holds an exclusive lock on path until the returned function is
// called. It is flock, the lock the executor's bootstrap takes on the same
// files (`remote_execution/executor_download.lock`).
func lockFile(path string) (func(), error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX); err != nil {
		file.Close()
		return nil, err
	}
	return func() { file.Close() }, nil
}
