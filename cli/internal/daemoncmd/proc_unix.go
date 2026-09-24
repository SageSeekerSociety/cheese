//go:build !windows

package daemoncmd

import (
	"errors"
	"fmt"
	"os"
	"syscall"
	"time"
)

// errNoInProcessUpdate is what signalUpdate returns where a running service
// cannot be asked to update itself. Never on this platform.
var errNoInProcessUpdate = errors.New("the running service cannot update itself in place")

// signalUpdate asks the running service to update itself in-process (SIGUSR2),
// so its private tmux and the tasks inside survive the binary swap.
func signalUpdate(pid int) error { return syscall.Kill(pid, syscall.SIGUSR2) }

// procAlive reports whether pid names a live process. EPERM means it exists but we may
// not signal it (a differently-owned process) — still alive; ESRCH means it is gone.
func procAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// stopProcess ensures pid is no longer running: SIGTERM (so the host tears its tmux
// down cleanly), then SIGKILL as a backstop, polling briefly between. Returns nil once
// the process is gone (or was never there), or an error if it is still alive after both
// — e.g. we lack the privilege to signal a differently-owned process.
func stopProcess(pid int) error {
	if !procAlive(pid) {
		return nil
	}
	_ = syscall.Kill(pid, syscall.SIGTERM)
	for range 30 {
		if !procAlive(pid) {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	_ = syscall.Kill(pid, syscall.SIGKILL)
	for range 20 {
		if !procAlive(pid) {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("process %d still alive", pid)
}

// removeSelf deletes the running binary; unlinking a running executable is fine here.
func removeSelf(exe string) error { return os.Remove(exe) }
