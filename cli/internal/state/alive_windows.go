package state

import "syscall"

// stillActive is the exit code Windows reports for a process that has not exited.
const stillActive = 259

// Alive reports whether pid names a running process. Windows has no signal 0:
// a process that exited can still be opened while anyone holds a handle to it,
// so the answer is its exit code, not whether it opens.
func Alive(pid int) bool {
	h, err := syscall.OpenProcess(syscall.PROCESS_QUERY_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer syscall.CloseHandle(h)
	var code uint32
	return syscall.GetExitCodeProcess(h, &code) == nil && code == stillActive
}
