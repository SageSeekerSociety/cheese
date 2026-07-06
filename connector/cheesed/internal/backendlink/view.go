package backendlink

import "context"

// FrameSink receives one complete webtty frame that must be relayed onward
// as a single WS BINARY message (mirrors webttybridge.FrameSink; kept as an
// independent type here so this package has no compile-time dependency on
// webttybridge's concrete implementation — only internal/app wires them
// together).
type FrameSink func(frame []byte) error

// ViewHandle is a running on-demand terminal view (contract §3): a pty
// attach bridged through webtty. It exists only while >=1 browser viewer is
// present.
type ViewHandle struct {
	// Feed delivers one inbound webtty frame (browser input) into the view.
	Feed func(frame []byte)
	// Resize resizes the underlying pty/webtty session.
	Resize func(cols, rows int) error
	// Stop tears the view down. The tmux session itself is left running.
	Stop func()
}

// TerminalView is the seam for spinning the on-demand pty+webtty bridge up
// and down, decoupling backendlink from tmuxmgr/webttybridge specifics.
type TerminalView interface {
	// Start begins relaying the terminal sized to cols x rows, shipping
	// every outbound webtty frame to out. It returns a handle used to feed
	// inbound frames, resize, and eventually stop the view.
	Start(ctx context.Context, cols, rows int, out FrameSink) (*ViewHandle, error)
}
