// Package app wires cheesed's seams together (constructor injection) and
// orchestrates startup/shutdown. It is the composition root: the one place
// allowed to depend on every other internal package's concrete
// implementation.
//
// cheesed is a thin, transparent relay. It launches the agent in a private
// tmux session and pumps that terminal over one dial-out WebSocket to the
// backend: screen frames go up, controller input comes down. It holds no
// policy -- no prompt answering, no idle detection, no local tool API. The
// backend owns all of that and drives the agent by sending input frames on
// the same channel a human controller uses.
package app

import (
	"context"
	"errors"
	"fmt"
	"log"
	"path/filepath"
	"time"

	"cheese/connector/cheesed/internal/backendlink"
	"cheese/connector/cheesed/internal/config"
	"cheese/connector/cheesed/internal/tmuxmgr"
)

const sessionName = "agent"

// App owns every long-lived component of a running cheesed daemon.
type App struct {
	cfg *config.Config

	tmux tmuxmgr.Tmux
	link *backendlink.Link
}

// New constructs an App from cfg: resolves the tmux binary, provisions the
// private socket directory, ensures the agent's tmux session exists, and
// wires the terminal view into the backend link.
func New(cfg *config.Config) (*App, error) {
	tmuxBin, err := cfg.ResolveTmux()
	if err != nil {
		return nil, fmt.Errorf("app: resolve tmux: %w", err)
	}

	runtimeDir, err := resolveRuntimeDir()
	if err != nil {
		return nil, fmt.Errorf("app: runtime dir: %w", err)
	}
	sockPath := filepath.Join(runtimeDir, "tmux.sock")

	tmux := tmuxmgr.New(tmuxBin, sockPath, sessionName)

	spec := tmuxmgr.SessionSpec{
		Name: sessionName,
		Path: cfg.Agent.Path,
		Args: cfg.Agent.Args,
		Cwd:  cfg.Agent.Cwd,
		Env:  cfg.Agent.Env,
		Cols: cfg.Cols,
		Rows: cfg.Rows,
	}
	if err := tmux.EnsureSession(spec); err != nil {
		return nil, fmt.Errorf("app: ensure session: %w", err)
	}

	view := newTmuxTerminalView(tmux, "claude-code")

	link := backendlink.New(
		cfg.BackendWSURL,
		cfg.SessionToken,
		"claude-code",
		cfg.Cols,
		cfg.Rows,
		time.Duration(cfg.HeartbeatIntervalMs)*time.Millisecond,
		view,
	)

	return &App{
		cfg:  cfg,
		tmux: tmux,
		link: link,
	}, nil
}

// Run maintains the backend link (reconnecting on drop) until ctx is
// cancelled or the link fails unrecoverably. On return it kills the tmux
// session per the daemon's graceful-shutdown contract.
func (a *App) Run(ctx context.Context) error {
	err := a.link.Run(ctx)

	a.shutdown()

	if err != nil && !errors.Is(err, context.Canceled) {
		return fmt.Errorf("app: backend link: %w", err)
	}
	return nil
}

// shutdown kills the tmux session (and, if it was the last one, the private
// tmux server behind it exits on its own).
func (a *App) shutdown() {
	if err := a.tmux.Kill(); err != nil {
		log.Printf("cheesed: tmux kill: %v", err)
	}
}
