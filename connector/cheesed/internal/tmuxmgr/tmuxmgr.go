// Package tmuxmgr drives a tmux session that lives on a PRIVATE socket
// (contract §3): every command runs as `tmux -S <sock> ...` against a tmux
// server that is invisible to the operator's own `tmux ls`. The agent runs
// detached inside that session so it survives independent of any particular
// pty attachment; a pty attach is only opened on demand by the caller.
package tmuxmgr

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/creack/pty"
)

// SessionSpec describes the command to launch as the tmux session's initial
// process.
type SessionSpec struct {
	// Name is the tmux session name.
	Name string
	// Path is the executable to run (e.g. the agent binary).
	Path string
	// Args are the arguments passed to Path.
	Args []string
	// Cwd is the working directory the session's process starts in.
	Cwd string
	// Env holds extra "KEY=VALUE" environment entries to export into the
	// session before launching Path.
	Env []string
	// Cols/Rows size the initial (hidden) session window.
	Cols int
	Rows int
}

// Tmux is the seam callers depend on, so tests and future callers can swap
// in a fake without caring about the concrete process-exec implementation.
type Tmux interface {
	// EnsureSession creates the session described by spec if it does not
	// already exist. It is a no-op if the session is already running.
	EnsureSession(spec SessionSpec) error
	// HasSession reports whether the session currently exists.
	HasSession() (bool, error)
	// Kill destroys the session (and, if it is the last one, the private
	// tmux server behind sockPath will exit on its own).
	Kill() error
	// Attach opens a pty running `tmux -S sock attach -t <name>` sized to
	// cols x rows. It returns the pty master file plus a closer that
	// detaches the attach client (the session itself is left running).
	Attach(cols, rows int) (*os.File, func() error, error)
}

// Manager is the default Tmux implementation, invoking the tmux binary
// against a private, per-daemon UNIX socket.
type Manager struct {
	tmuxBin     string
	sockPath    string
	sessionName string
}

// New builds a Manager that will drive tmuxBin against the private socket at
// sockPath, operating on the session named sessionName.
func New(tmuxBin, sockPath, sessionName string) *Manager {
	return &Manager{
		tmuxBin:     tmuxBin,
		sockPath:    sockPath,
		sessionName: sessionName,
	}
}

var _ Tmux = (*Manager)(nil)

func (m *Manager) command(args ...string) *exec.Cmd {
	full := append([]string{"-S", m.sockPath}, args...)
	cmd := exec.Command(m.tmuxBin, full...)
	cmd.Env = tmuxBaseEnv()
	return cmd
}

// tmuxBaseEnv is the environment for every tmux invocation, forcing a UTF-8
// locale. cheesed is frequently launched from a non-interactive shell whose
// LANG is "C" (not UTF-8); a tmux client inheriting that is treated as
// non-UTF-8, and tmux then renders EVERY multibyte glyph (box-drawing, arcs,
// Braille spinners, symbols) as an underscore '_'. Forcing C.UTF-8 -- which is
// built into glibc and needs no locale generation -- keeps the whole pipeline
// UTF-8 clean, both for the server (new-session) and each attach client.
func tmuxBaseEnv() []string {
	return append(os.Environ(), "LC_ALL=C.UTF-8", "LANG=C.UTF-8")
}

// HasSession reports whether the session currently exists on the private
// server.
func (m *Manager) HasSession() (bool, error) {
	cmd := m.command("has-session", "-t", m.sessionName)
	err := cmd.Run()
	if err == nil {
		return true, nil
	}
	var exitErr *exec.ExitError
	if isExitError(err, &exitErr) {
		// tmux has-session exits non-zero when the session is absent;
		// that is a normal, expected outcome, not a failure to report.
		return false, nil
	}
	return false, fmt.Errorf("tmuxmgr: has-session: %w", err)
}

// EnsureSession creates spec's session if it is not already running.
func (m *Manager) EnsureSession(spec SessionSpec) error {
	exists, err := m.HasSession()
	if err != nil {
		return err
	}
	if exists {
		return nil
	}

	if err := os.MkdirAll(dirOf(m.sockPath), 0o700); err != nil {
		return fmt.Errorf("tmuxmgr: prepare socket dir: %w", err)
	}

	cols := spec.Cols
	if cols <= 0 {
		cols = 200
	}
	rows := spec.Rows
	if rows <= 0 {
		rows = 50
	}

	args := []string{
		"new-session", "-d",
		"-s", spec.Name,
		"-x", fmt.Sprintf("%d", cols),
		"-y", fmt.Sprintf("%d", rows),
	}
	if spec.Cwd != "" {
		args = append(args, "-c", spec.Cwd)
	}

	// Prefix the command line with `env KEY=VAL ...` so the launched
	// process picks up the requested environment without requiring the
	// tmux server itself to inherit it.
	cmdLine := append([]string{}, args...)
	innerCmd := buildEnvPrefixedCommand(spec.Env, spec.Path, spec.Args)
	cmdLine = append(cmdLine, innerCmd...)

	cmd := m.command(cmdLine...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("tmuxmgr: new-session: %w: %s", err, strings.TrimSpace(stderr.String()))
	}

	// Best-effort: enable mouse so wheel-scroll works. Coding agents such as
	// Claude Code detect tmux and defer scrolling to it, expecting `mouse on`;
	// with it, a (controlling) browser viewer's wheel drives tmux copy-mode
	// scrollback. Non-fatal if it fails -- the session is already usable.
	_ = m.command("set-option", "-g", "mouse", "on").Run()
	return nil
}

// buildEnvPrefixedCommand returns the argv that, appended after
// `new-session ...`, launches path/args with env exported via `env`.
func buildEnvPrefixedCommand(env []string, path string, args []string) []string {
	if len(env) == 0 {
		return append([]string{path}, args...)
	}
	out := append([]string{"env"}, env...)
	out = append(out, path)
	out = append(out, args...)
	return out
}

// Kill destroys the session. If it was the private server's last session,
// the private tmux server process exits on its own shortly after.
func (m *Manager) Kill() error {
	cmd := m.command("kill-session", "-t", m.sessionName)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if exists, herr := m.HasSession(); herr == nil && !exists {
			// Already gone: killing an absent session is not an error
			// for our purposes.
			return nil
		}
		return fmt.Errorf("tmuxmgr: kill-session: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return nil
}

// Attach opens a pty-backed `tmux -S sock attach -t <name>` client sized to
// cols x rows. The returned closer detaches the client (Ctrl-b d equivalent,
// via SIGHUP on the client process) and closes the pty; the tmux session
// itself keeps running detached.
func (m *Manager) Attach(cols, rows int) (*os.File, func() error, error) {
	cmd := m.command("attach-session", "-t", m.sessionName)
	// The tmux attach *client* needs a usable TERM to select a terminfo entry
	// (else tmux aborts with "terminal does not support clear") and a UTF-8
	// locale (else every multibyte glyph renders as '_'); tmuxBaseEnv supplies
	// the locale, and we add TERM on top.
	cmd.Env = append(tmuxBaseEnv(), "TERM=xterm-256color")

	ptyFile, err := pty.StartWithSize(cmd, &pty.Winsize{
		Rows: uint16(rows),
		Cols: uint16(cols),
	})
	if err != nil {
		return nil, nil, fmt.Errorf("tmuxmgr: attach: %w", err)
	}

	closer := func() error {
		_ = ptyFile.Close()
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
			_, _ = cmd.Process.Wait()
		}
		return nil
	}

	return ptyFile, closer, nil
}

func dirOf(p string) string {
	i := strings.LastIndexByte(p, '/')
	if i < 0 {
		return "."
	}
	return p[:i]
}

func isExitError(err error, target **exec.ExitError) bool {
	ee, ok := err.(*exec.ExitError)
	if ok {
		*target = ee
	}
	return ok
}
