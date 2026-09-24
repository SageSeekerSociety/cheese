//go:build !windows

package state

import (
	"os"
	"syscall"
)

// Alive reports whether pid names a process this account can signal.
func Alive(pid int) bool {
	p, err := os.FindProcess(pid)
	return err == nil && p.Signal(syscall.Signal(0)) == nil
}
