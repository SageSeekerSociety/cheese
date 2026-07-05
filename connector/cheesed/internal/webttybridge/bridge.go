// Package webttybridge wires gotty's webtty package (contract §4) between
// two ends:
//   - a Master fed by frames arriving over the backend link (§5) and that
//     ships outbound frames back out through an injected sink, and
//   - a Slave wrapping the attach pty obtained from tmuxmgr.
//
// The bridge is only ever spun up on demand, while >=1 browser viewer is
// present (contract §3); tearing it down leaves the tmux session running.
package webttybridge

import (
	"context"
	"io"
	"os"
	"sync"

	"github.com/creack/pty"
	"github.com/sorenisanerd/gotty/webtty"
)

// FrameSink receives one complete webtty frame (opcode byte + payload) that
// must be relayed onward as a single WS BINARY message.
type FrameSink func(frame []byte) error

// ChannelMaster implements webtty.Master (io.ReadWriter) by bridging frames
// fed in from the backend link (Feed) to webtty's Read loop, and by handing
// every frame webtty writes (terminal output, pings, etc.) to an injected
// FrameSink.
type ChannelMaster struct {
	in        chan []byte
	out       FrameSink
	closed    chan struct{}
	closeOnce sync.Once
}

// NewChannelMaster builds a ChannelMaster that ships every frame webtty
// writes through out.
func NewChannelMaster(out FrameSink) *ChannelMaster {
	return &ChannelMaster{
		in:     make(chan []byte, 64),
		out:    out,
		closed: make(chan struct{}),
	}
}

var _ webtty.Master = (*ChannelMaster)(nil)

// Feed delivers one inbound webtty frame (e.g. controller Input, a resize,
// or a ping) coming from the backend link, to be consumed by webtty's
// Read loop. It is safe to call after Close (it becomes a no-op).
func (m *ChannelMaster) Feed(frame []byte) {
	select {
	case m.in <- frame:
	case <-m.closed:
	}
}

// Read implements io.Reader by handing back exactly one previously-fed
// frame per call, matching webtty's one-frame-per-Read framing contract.
func (m *ChannelMaster) Read(p []byte) (int, error) {
	select {
	case frame, ok := <-m.in:
		if !ok {
			return 0, io.EOF
		}
		n := copy(p, frame)
		return n, nil
	case <-m.closed:
		return 0, io.EOF
	}
}

// Write implements io.Writer by handing the full frame webtty produced
// (e.g. Output, Pong, SetWindowTitle) to the injected sink.
func (m *ChannelMaster) Write(p []byte) (int, error) {
	frame := append([]byte(nil), p...)
	if err := m.out(frame); err != nil {
		return 0, err
	}
	return len(p), nil
}

// Close unblocks any pending Read/Feed calls with io.EOF.
func (m *ChannelMaster) Close() error {
	m.closeOnce.Do(func() { close(m.closed) })
	return nil
}

// PtySlave adapts an attach pty (from tmuxmgr.Manager.Attach) to
// webtty.Slave.
type PtySlave struct {
	f     *os.File
	title map[string]interface{}
}

// NewPtySlave wraps f (an attach pty) as a webtty.Slave. title is returned
// verbatim from WindowTitleVariables.
func NewPtySlave(f *os.File, title map[string]interface{}) *PtySlave {
	if title == nil {
		title = map[string]interface{}{}
	}
	return &PtySlave{f: f, title: title}
}

var _ webtty.Slave = (*PtySlave)(nil)

func (s *PtySlave) Read(p []byte) (int, error)  { return s.f.Read(p) }
func (s *PtySlave) Write(p []byte) (int, error) { return s.f.Write(p) }

// WindowTitleVariables returns the static title variables supplied at
// construction time.
func (s *PtySlave) WindowTitleVariables() map[string]interface{} {
	return s.title
}

// ResizeTerminal resizes the underlying pty.
func (s *PtySlave) ResizeTerminal(columns int, rows int) error {
	return pty.Setsize(s.f, &pty.Winsize{
		Rows: uint16(rows),
		Cols: uint16(columns),
	})
}

// Bridge owns a single webtty.WebTTY instance relaying between a
// ChannelMaster and a PtySlave. It is always permit-write (contract §1):
// cheesed never has a read-only mode, arbitration of "who may type" is a
// backend/frontend concern.
type Bridge struct {
	wt *webtty.WebTTY
}

// New builds a Bridge. cols x rows is only the initial pty geometry (already
// applied by tmuxmgr.Attach); we deliberately do NOT pass
// WithFixedColumns/WithFixedRows, because those make gotty's webtty ignore the
// browser's ResizeTerminal ('3') frames entirely -- freezing the terminal at
// the initial size and garbling any viewport of a different size. With them
// omitted, each browser resize flows through to PtySlave.ResizeTerminal, which
// resizes the attach pty (and thus the tmux window) to match the viewer.
func New(master webtty.Master, slave webtty.Slave, cols, rows int) (*Bridge, error) {
	_ = cols
	_ = rows
	wt, err := webtty.New(master, slave,
		webtty.WithPermitWrite(),
	)
	if err != nil {
		return nil, err
	}
	return &Bridge{wt: wt}, nil
}

// Run blocks relaying master<->slave until ctx is cancelled or either side
// closes (returns webtty.ErrMasterClosed / webtty.ErrSlaveClosed).
func (b *Bridge) Run(ctx context.Context) error {
	return b.wt.Run(ctx)
}
