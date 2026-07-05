// Package app wires cheesed's seams together (constructor injection) and
// orchestrates startup/shutdown. It is the composition root: it is the one
// place allowed to depend on every other internal package's concrete
// implementation.
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
	"cheese/connector/cheesed/internal/driver"
	"cheese/connector/cheesed/internal/localapi"
	"cheese/connector/cheesed/internal/tmuxmgr"
)

const sessionName = "agent"

// App owns every long-lived component of a running cheesed daemon.
type App struct {
	cfg *config.Config

	tmux   tmuxmgr.Tmux
	driver *driver.AutoDriver
	link   *backendlink.Link
	api    *localapi.Server
}

// New constructs an App from cfg: resolves the tmux binary, provisions the
// private socket directory, ensures the agent's tmux session exists, and
// wires the driver/link/local-API seams together.
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

	// backendlink.Driver and driver.AutoDriver's StatusFunc are mutually
	// referential (the driver reports status through the link; the link
	// pauses/resumes the driver on takeover), so the link is constructed
	// after the driver via a forwarding closure captured by reference.
	var link *backendlink.Link
	statusFn := func(phase, detail string) {
		if link != nil {
			link.SendStatus(phase, detail)
		}
	}

	drv := driver.New(
		tmux,
		cfg.PromptRules,
		cfg.QuiescenceN,
		time.Duration(cfg.PollIntervalMs)*time.Millisecond,
		statusFn,
	)

	view := newTmuxTerminalView(tmux, "claude-code")

	link = backendlink.New(
		cfg.BackendWSURL,
		cfg.SessionToken,
		"claude-code",
		cfg.Cols,
		cfg.Rows,
		time.Duration(cfg.HeartbeatIntervalMs)*time.Millisecond,
		drv,
		view,
	)

	proxy := localapi.NewHTTPToolProxy(cfg.BackendHTTPURL, cfg.SessionToken)
	api := localapi.New(cfg.LocalAPIPort, proxy)

	return &App{
		cfg:    cfg,
		tmux:   tmux,
		driver: drv,
		link:   link,
		api:    api,
	}, nil
}

// Run starts the local API, the auto-driver, and the backend link, and
// blocks until ctx is cancelled or a component fails unrecoverably. On
// return it tears down every component, including killing the tmux
// session, per the daemon's graceful-shutdown contract.
func (a *App) Run(ctx context.Context) error {
	apiErrCh, err := a.api.Start()
	if err != nil {
		return fmt.Errorf("app: start local api: %w", err)
	}

	driverCtx, driverCancel := context.WithCancel(ctx)
	defer driverCancel()
	go a.driver.Run(driverCtx)

	linkErrCh := make(chan error, 1)
	go func() {
		linkErrCh <- a.link.Run(ctx)
	}()

	var runErr error
	select {
	case <-ctx.Done():
	case err := <-apiErrCh:
		if err != nil {
			runErr = fmt.Errorf("app: local api: %w", err)
		}
	case err := <-linkErrCh:
		if err != nil && !errors.Is(err, context.Canceled) {
			runErr = fmt.Errorf("app: backend link: %w", err)
		}
	}

	a.shutdown()
	return runErr
}

// shutdown tears every component down: local API, backend link/terminal
// view (already handled by Run's context cancellation), and finally the
// tmux session itself.
func (a *App) shutdown() {
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := a.api.Shutdown(shutdownCtx); err != nil {
		log.Printf("cheesed: local api shutdown: %v", err)
	}

	if err := a.tmux.Kill(); err != nil {
		log.Printf("cheesed: tmux kill: %v", err)
	}
}
