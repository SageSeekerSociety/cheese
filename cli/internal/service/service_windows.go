// Package service keeps `cheesehost` running in the background. On Windows it
// is a per-user login entry (HKCU\…\Run) plus a hidden process started now —
// the counterpart of the systemd user unit and LaunchAgent elsewhere, and like
// them it never asks for administrator rights. A Windows service would: it is
// machine-wide by definition.
package service

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/devenv"
	"github.com/SageSeekerSociety/cheese/cli/internal/host"
	"github.com/SageSeekerSociety/cheese/cli/internal/state"
	"github.com/SageSeekerSociety/cheese/cli/internal/update"
)

const (
	runKey    = `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
	valueName = "cheese"

	createNoWindow        = 0x08000000
	createNewProcessGroup = 0x00000200
)

// Control runs an install/uninstall/start/stop/restart action.
func Control(cfgPath, action string) error {
	switch action {
	case "install":
		return install(cfgPath)
	case "uninstall":
		err := hidden("reg.exe", "delete", runKey, "/v", valueName, "/f").Run()
		if err != nil && installed() {
			return fmt.Errorf("remove the login entry: %w", err)
		}
		return nil
	case "start":
		return start(cfgPath)
	case "stop":
		return stop(cfgPath)
	case "restart":
		if err := stop(cfgPath); err != nil {
			return err
		}
		return start(cfgPath)
	}
	return fmt.Errorf("unknown service action %q", action)
}

// install writes the login entry. conhost --headless gives the console program
// the console it expects without a window appearing at every login.
func install(cfgPath string) error {
	self, err := update.SelfPath()
	if err != nil {
		return err
	}
	command := fmt.Sprintf(`conhost.exe --headless "%s" run --config "%s"`, self, cfgPath)
	if out, err := hidden("reg.exe", "add", runKey, "/v", valueName, "/t", "REG_SZ", "/d", command, "/f").CombinedOutput(); err != nil {
		return fmt.Errorf("write the login entry: %v: %s", err, out)
	}
	return nil
}

func installed() bool {
	return hidden("reg.exe", "query", runKey, "/v", valueName).Run() == nil
}

// start runs `cheesehost run` now, detached and windowless, so it outlives
// whatever started it — the terminal or the desktop app. Its output goes to a
// log beside the config, the only place anyone could read it.
func start(cfgPath string) error {
	if state.PID(cfgPath) > 0 {
		return nil
	}
	self, err := update.SelfPath()
	if err != nil {
		return err
	}
	logFile, err := os.OpenFile(filepath.Join(filepath.Dir(cfgPath), "cheese.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	cmd := hidden(self, "run", "--config", cfgPath)
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.SysProcAttr.CreationFlags |= createNewProcessGroup
	return cmd.Start()
}

func stop(cfgPath string) error {
	pid := state.RawPID(cfgPath)
	if pid <= 0 || !state.Alive(pid) {
		return nil
	}
	if p, err := os.FindProcess(pid); err == nil {
		_ = p.Kill()
	}
	for range 30 {
		if !state.Alive(pid) {
			state.Clear(cfgPath)
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("the connector (pid %d) did not stop", pid)
}

// RunForeground runs the host in this process (`cheesehost run`): the login
// entry and start both land here. The runtime the server's commands need is
// placed first, so the machine is only announced once it can do the work.
func RunForeground(cfgPath string) error {
	if other := state.PID(cfgPath); other > 0 && other != os.Getpid() {
		return nil // already running; two would share one credential
	}
	if self, err := update.SelfPath(); err == nil {
		update.CleanupReplaced(self)
	}
	cfg, err := host.LoadConfig(cfgPath)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	for {
		err := devenv.Ensure(ctx, cfg.Base, os.Stderr)
		if err == nil {
			break
		}
		if ctx.Err() != nil {
			return nil
		}
		fmt.Fprintf(os.Stderr, "cheese: runtime not ready, retrying in a minute: %v\n", err)
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(time.Minute):
		}
	}
	h, err := host.New(cfg, cfgPath)
	if err != nil {
		return err
	}
	if err := h.Run(ctx); err != nil && !errors.Is(err, context.Canceled) {
		return err
	}
	return nil
}

// Status reports the connector's state in the words the other platforms use.
func Status(cfgPath string) (string, error) {
	switch {
	case state.PID(cfgPath) > 0:
		return "running", nil
	case installed():
		return "stopped", nil
	}
	return "not installed", nil
}

// KeepRunningAfterLogout: the login entry starts the connector with the user's
// session, which is the answer on Windows as it is for a LaunchAgent.
func KeepRunningAfterLogout() error { return nil }

func hidden(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	return cmd
}
