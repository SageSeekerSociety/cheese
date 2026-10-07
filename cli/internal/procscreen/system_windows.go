//go:build windows

package procscreen

import (
	"errors"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
)

const (
	createNoWindow         = 0x08000000
	createNewProcessGroup  = 0x00000200
	createBreakawayFromJob = 0x01000000
	stillActive            = 259
)

// New keeps this machine's screens under dir.
func New(dir string) (*Manager, error) { return newManager(dir, winSystem{}) }

type winSystem struct{}

// start runs the program detached from the connector: no window, a process
// group of its own, and out of the connector's job when the job allows it, so
// that stopping or updating the connector leaves it running.
func (winSystem) start(argv, env []string, log string) (int, int64, error) {
	out, err := os.OpenFile(log, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return 0, 0, err
	}
	defer out.Close()
	launch := func(flags uint32) (*exec.Cmd, error) {
		cmd := exec.Command(argv[0], argv[1:]...)
		cmd.Env = append(os.Environ(), env...)
		cmd.Stdout, cmd.Stderr = out, out
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: flags}
		return cmd, cmd.Start()
	}
	cmd, err := launch(createNoWindow | createNewProcessGroup | createBreakawayFromJob)
	if err != nil && errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		// A job that forbids breaking away: the program stays in it.
		cmd, err = launch(createNoWindow | createNewProcessGroup)
	}
	if err != nil {
		return 0, 0, err
	}
	pid := cmd.Process.Pid
	started := processStart(pid)
	_ = cmd.Process.Release()
	return pid, started, nil
}

// processStart is when the process began, as a FILETIME count; 0 when it is gone.
func processStart(pid int) int64 {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return 0
	}
	defer windows.CloseHandle(h)
	var created, exited, kernel, user windows.Filetime
	if windows.GetProcessTimes(h, &created, &exited, &kernel, &user) != nil {
		return 0
	}
	return int64(created.HighDateTime)<<32 | int64(created.LowDateTime)
}

func (winSystem) running(pid int, started int64) bool {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var code uint32
	if windows.GetExitCodeProcess(h, &code) != nil || code != stillActive {
		return false
	}
	var created, exited, kernel, user windows.Filetime
	if windows.GetProcessTimes(h, &created, &exited, &kernel, &user) != nil {
		return false
	}
	return int64(created.HighDateTime)<<32|int64(created.LowDateTime) == started
}

// end ends the process tree: the launch script, the runner and the agent it holds.
func (winSystem) end(pid int) error {
	cmd := exec.Command("taskkill", "/PID", strconv.Itoa(pid), "/T", "/F")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	_ = cmd.Run()
	return nil
}

var tickCount64 = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetTickCount64")

func (winSystem) booted() int64 {
	now := windows.NsecToFiletime(time.Now().UnixNano())
	nowCount := int64(now.HighDateTime)<<32 | int64(now.LowDateTime)
	// FILETIME counts 100 ns; the tick count is milliseconds since boot.
	ms, _, _ := tickCount64.Call()
	return nowCount - int64(ms)*10_000
}
