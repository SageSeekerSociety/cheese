package backendlink

import (
	"context"
	"encoding/json"
)

// handleControl demultiplexes one inbound WS TEXT message into the
// takeover/drive/resize/viewer actions defined by contract §5.
func (l *Link) handleControl(ctx context.Context, data []byte) {
	var env inboundEnvelope
	if err := json.Unmarshal(data, &env); err != nil {
		return
	}

	switch env.T {
	case "takeover":
		if env.On == nil {
			return
		}
		if *env.On {
			l.driver.Pause()
		} else {
			l.driver.Resume()
		}

	case "drive":
		_ = l.driver.Drive(env.Keys)

	case "resize":
		l.handleResize(env.Cols, env.Rows)

	case "viewer":
		if env.Present == nil {
			return
		}
		if *env.Present {
			l.startView(ctx)
		} else {
			l.stopView()
		}
	}
}

// handleBinary routes one inbound WS BINARY message (a controller's webtty
// input frame) into the active terminal view, if any. If there is no
// active view (e.g. a race between the last viewer leaving and a stray
// input frame), the frame is dropped: cheesed stays a dumb, transparent
// relay and never buffers input for a view that isn't there.
func (l *Link) handleBinary(frame []byte) {
	l.mu.Lock()
	handle := l.activeView
	l.mu.Unlock()
	if handle == nil {
		return
	}
	handle.Feed(frame)
}

// startView spins up the on-demand terminal view (contract §3) if one
// isn't already running.
func (l *Link) startView(parent context.Context) {
	l.mu.Lock()
	if l.activeView != nil {
		l.mu.Unlock()
		return
	}
	cols, rows := l.viewCols, l.viewRows
	l.mu.Unlock()

	viewCtx, cancel := context.WithCancel(parent)
	handle, err := l.view.Start(viewCtx, cols, rows, l.writeBinary)
	if err != nil {
		cancel()
		l.SendStatus("running", "view start failed: "+err.Error())
		return
	}

	l.mu.Lock()
	l.activeView = handle
	l.viewCancel = cancel
	l.mu.Unlock()
}

// stopView tears the on-demand terminal view down, leaving the tmux
// session itself running.
func (l *Link) stopView() {
	l.mu.Lock()
	handle := l.activeView
	cancel := l.viewCancel
	l.activeView = nil
	l.viewCancel = nil
	l.mu.Unlock()

	if handle != nil {
		handle.Stop()
	}
	if cancel != nil {
		cancel()
	}
}

// handleResize updates the tracked terminal size and, if a view is active,
// resizes it live.
func (l *Link) handleResize(cols, rows int) {
	if cols <= 0 || rows <= 0 {
		return
	}
	l.mu.Lock()
	l.viewCols, l.viewRows = cols, rows
	handle := l.activeView
	l.mu.Unlock()

	if handle != nil {
		_ = handle.Resize(cols, rows)
	}
}
