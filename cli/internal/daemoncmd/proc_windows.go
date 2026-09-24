package daemoncmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"syscall"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/state"
)

// errNoInProcessUpdate: Windows has no signal to ask the running service to
// update itself, and it hosts no screens that would need to survive one, so
// `update` stops it, replaces the binary and starts it again.
var errNoInProcessUpdate = errors.New("the running service cannot update itself in place")

func signalUpdate(int) error { return errNoInProcessUpdate }

func procAlive(pid int) bool { return pid > 0 && state.Alive(pid) }

// stopProcess ends pid. There is no gentler signal to send first, and the
// connector keeps nothing on Windows that a hard stop would lose.
func stopProcess(pid int) error {
	if !procAlive(pid) {
		return nil
	}
	if p, err := os.FindProcess(pid); err == nil {
		_ = p.Kill()
	}
	for range 30 {
		if !procAlive(pid) {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("process %d still alive", pid)
}

// removeSelf deletes the running binary. Windows will not delete an executable
// that is running, and `uninstall` is that executable: it moves itself aside
// (allowed while running) and leaves a hidden cmd to delete the file once this
// process has exited.
func removeSelf(exe string) error {
	doomed := exe + ".uninstalled"
	if err := os.Rename(exe, doomed); err != nil {
		return err
	}
	cmd := exec.Command("cmd.exe", "/c", "ping -n 3 127.0.0.1 >nul & del /f /q \""+doomed+"\"")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	return cmd.Start()
}

const createNoWindow = 0x08000000
