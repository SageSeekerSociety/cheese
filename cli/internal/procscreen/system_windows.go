//go:build windows

package procscreen

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"
	"unsafe"

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
	return handleStart(h)
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
	return started != 0 && handleStart(h) == started
}

// endWait is how long end waits for the ended tree to be gone.
const endWait = 10 * time.Second

// end ends the process tree: the launch script, the runner and the agent it
// holds. taskkill only asks for the end, and walks the tree through live
// parents only, so it misses a process whose parent has already exited. end
// also ends every process it holds itself, and waits for each to be gone before
// it returns: a process told to end holds its files until it is gone, and the
// screen's log is removed after end.
func (winSystem) end(pid int) error {
	tree := openTree(uint32(pid))
	defer func() {
		for _, h := range tree {
			_ = windows.CloseHandle(h)
		}
	}()
	cmd := exec.Command("taskkill", "/PID", strconv.Itoa(pid), "/T", "/F")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	_ = cmd.Run()
	for _, h := range tree {
		_ = windows.TerminateProcess(h, 1)
	}
	deadline := time.Now().Add(endWait)
	for _, h := range tree {
		left := max(time.Until(deadline), 0)
		if ev, err := windows.WaitForSingleObject(h, uint32(left/time.Millisecond)); err != nil || ev != windows.WAIT_OBJECT_0 {
			return fmt.Errorf("process %d and what it started still run %s after they were ended", pid, endWait)
		}
	}
	return nil
}

// openTree holds a handle to pid and to every process it started, theirs in
// turn, so that ending and waiting on them reaches the very processes of the
// tree, even once their pids are free for others. A process counts as started by a
// parent only if it began after it: a parent pid can be one reused since.
func openTree(pid uint32) []windows.Handle {
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil
	}
	defer windows.CloseHandle(snap)
	children := map[uint32][]uint32{}
	entry := windows.ProcessEntry32{Size: uint32(unsafe.Sizeof(windows.ProcessEntry32{}))}
	for err = windows.Process32First(snap, &entry); err == nil; err = windows.Process32Next(snap, &entry) {
		if entry.ProcessID != entry.ParentProcessID {
			children[entry.ParentProcessID] = append(children[entry.ParentProcessID], entry.ProcessID)
		}
	}
	var held []windows.Handle
	var visit func(pid uint32, parentStart int64)
	visit = func(pid uint32, parentStart int64) {
		const wait = windows.SYNCHRONIZE | windows.PROCESS_QUERY_LIMITED_INFORMATION
		h, err := windows.OpenProcess(wait|windows.PROCESS_TERMINATE, false, pid)
		if err != nil {
			// One that may not be ended from here is still waited for.
			if h, err = windows.OpenProcess(wait, false, pid); err != nil {
				return
			}
		}
		start := handleStart(h)
		if start < parentStart {
			_ = windows.CloseHandle(h)
			return
		}
		held = append(held, h)
		for _, child := range children[pid] {
			visit(child, start)
		}
	}
	visit(pid, 0)
	return held
}

// handleStart is when the process behind h began, as a FILETIME count.
func handleStart(h windows.Handle) int64 {
	var created, exited, kernel, user windows.Filetime
	if windows.GetProcessTimes(h, &created, &exited, &kernel, &user) != nil {
		return 0
	}
	return int64(created.HighDateTime)<<32 | int64(created.LowDateTime)
}

var tickCount64 = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetTickCount64")

func (winSystem) booted() int64 {
	now := windows.NsecToFiletime(time.Now().UnixNano())
	nowCount := int64(now.HighDateTime)<<32 | int64(now.LowDateTime)
	// FILETIME counts 100 ns; the tick count is milliseconds since boot.
	ms, _, _ := tickCount64.Call()
	return nowCount - int64(ms)*10_000
}
