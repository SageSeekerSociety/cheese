package daemoncmd

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
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
	dropFromUserPath(filepath.Dir(exe))
	doomed := exe + ".uninstalled"
	if err := os.Rename(exe, doomed); err != nil {
		return err
	}
	cmd := exec.Command("cmd.exe", "/c", "ping -n 3 127.0.0.1 >nul & del /f /q \""+doomed+"\"")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	return cmd.Start()
}

const createNoWindow = 0x08000000

// stopFootprintProcesses ends every process started from a program under
// root — the executor's python, claude, the shell and tools it placed. Windows
// will not delete a file a running program was started from, so without this
// uninstall stops halfway with the rooms still running.
func stopFootprintProcesses(root string) {
	// The root travels in the environment: -Command folds any argument after
	// it into the script text.
	script := `Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:CHEESE_FOOTPRINT + '\', [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	cmd.Env = append(os.Environ(), "CHEESE_FOOTPRINT="+root)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	_ = cmd.Run()
	// Handles close a moment after the process is gone.
	time.Sleep(time.Second)
}

// removeTree deletes dir. Git marks its objects read-only, and Windows will not
// delete a read-only file, so what the first pass leaves is made writable and
// the pass repeated; a handle still closing gets the same second chance.
func removeTree(dir string) error {
	err := os.RemoveAll(dir)
	for attempt := 0; err != nil && attempt < 3; attempt++ {
		_ = filepath.WalkDir(dir, func(path string, _ fs.DirEntry, _ error) error {
			_ = os.Chmod(path, 0o666)
			return nil
		})
		time.Sleep(500 * time.Millisecond)
		err = os.RemoveAll(dir)
	}
	return err
}

// dropFromUserPath takes dir out of the user's PATH, where install.ps1 put it.
// The directory travels in the environment, as in stopFootprintProcesses.
func dropFromUserPath(dir string) {
	script := `$p = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($p) { $kept = ($p -split ';' | Where-Object { $_ -and $_ -ne $env:CHEESE_BIN }) -join ';'; if ($kept -ne $p) { [Environment]::SetEnvironmentVariable('Path', $kept, 'User') } }`
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	cmd.Env = append(os.Environ(), "CHEESE_BIN="+dir)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	_ = cmd.Run()
}
