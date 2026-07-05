// Package driver implements the always-on auto-driver: it polls the tmux
// pane, answers known prompts, and reports coarse status (running/idle/
// prompt) to the backend. It is pausable so the backend can hand control to
// a human "controller" during takeover (contract §1) without the two
// fighting over the pane.
package driver

import (
	"context"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"cheese/connector/cheesed/internal/config"
	"cheese/connector/cheesed/internal/tmuxmgr"
)

// StatusFunc reports a coarse driver phase upstream (mirrors the backend
// link's {"t":"status"} message, contract §5).
type StatusFunc func(phase, detail string)

// Driver is the seam callers depend on so the concrete polling
// implementation can be swapped (e.g. in tests).
type Driver interface {
	// Run blocks polling until ctx is cancelled.
	Run(ctx context.Context)
	// Pause suspends automatic prompt-answering (e.g. during takeover).
	Pause()
	// Resume re-enables automatic prompt-answering.
	Resume()
	// Drive executes an orchestrator-issued key sequence immediately,
	// regardless of paused state (contract §5, {"t":"drive"}).
	Drive(keys []string) error
}

// AutoDriver is the default Driver: a ticker polls CapturePane, matches
// prompt_rules, and answers via SendKeys; quiescence (N stable snapshots in
// a row) is reported as "idle".
type AutoDriver struct {
	tmux         tmuxmgr.Tmux
	rules        []config.PromptRule
	quiescenceN  int
	pollInterval time.Duration
	status       StatusFunc

	paused atomic.Bool

	mu        sync.Mutex
	snapshots []string
}

var _ Driver = (*AutoDriver)(nil)

// New builds an AutoDriver.
func New(tmux tmuxmgr.Tmux, rules []config.PromptRule, quiescenceN int, pollInterval time.Duration, status StatusFunc) *AutoDriver {
	if status == nil {
		status = func(string, string) {}
	}
	return &AutoDriver{
		tmux:         tmux,
		rules:        rules,
		quiescenceN:  quiescenceN,
		pollInterval: pollInterval,
		status:       status,
	}
}

// Run polls every pollInterval until ctx is cancelled.
func (d *AutoDriver) Run(ctx context.Context) {
	ticker := time.NewTicker(d.pollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if d.paused.Load() {
				continue
			}
			d.tick()
		}
	}
}

// Pause suspends automatic prompt-answering.
func (d *AutoDriver) Pause() { d.paused.Store(true) }

// Resume re-enables automatic prompt-answering and clears quiescence
// history so idle/running status reflects only fresh observations.
func (d *AutoDriver) Resume() {
	d.mu.Lock()
	d.snapshots = nil
	d.mu.Unlock()
	d.paused.Store(false)
}

// Drive executes keys immediately, bypassing the paused gate: it is always
// the orchestrator explicitly asking for this input to be sent.
func (d *AutoDriver) Drive(keys []string) error {
	return d.tmux.SendKeys(keys...)
}

func (d *AutoDriver) tick() {
	pane, err := d.tmux.CapturePane()
	if err != nil {
		d.status("running", "capture-pane error: "+err.Error())
		return
	}

	for _, rule := range d.rules {
		if rule.MatchSubstring == "" {
			continue
		}
		if strings.Contains(pane, rule.MatchSubstring) {
			d.status("prompt", rule.MatchSubstring)
			_ = d.tmux.SendKeys(rule.Keys...)
			d.resetSnapshots()
			return
		}
	}

	if d.recordAndCheckQuiescent(pane) {
		d.status("idle", "")
	} else {
		d.status("running", "")
	}
}

// recordAndCheckQuiescent appends pane to the sliding snapshot window and
// reports whether the window is full and entirely identical.
func (d *AutoDriver) recordAndCheckQuiescent(pane string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()

	d.snapshots = append(d.snapshots, pane)
	if len(d.snapshots) > d.quiescenceN {
		d.snapshots = d.snapshots[len(d.snapshots)-d.quiescenceN:]
	}
	if len(d.snapshots) < d.quiescenceN {
		return false
	}
	first := d.snapshots[0]
	for _, s := range d.snapshots[1:] {
		if s != first {
			return false
		}
	}
	return true
}

func (d *AutoDriver) resetSnapshots() {
	d.mu.Lock()
	d.snapshots = nil
	d.mu.Unlock()
}
