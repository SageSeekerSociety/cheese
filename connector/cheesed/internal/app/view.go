package app

import (
	"context"
	"sync"

	"cheese/connector/cheesed/internal/backendlink"
	"cheese/connector/cheesed/internal/tmuxmgr"
	"cheese/connector/cheesed/internal/webttybridge"
)

// tmuxTerminalView is the concrete backendlink.TerminalView: it attaches a
// pty to the tmux session on demand and bridges it through webtty. This is
// the only place tmuxmgr and webttybridge are wired together, keeping both
// packages, and backendlink, independently testable.
type tmuxTerminalView struct {
	tmux      tmuxmgr.Tmux
	titleVars map[string]interface{}
}

func newTmuxTerminalView(tmux tmuxmgr.Tmux, agentName string) *tmuxTerminalView {
	return &tmuxTerminalView{
		tmux:      tmux,
		titleVars: map[string]interface{}{"agent": agentName},
	}
}

var _ backendlink.TerminalView = (*tmuxTerminalView)(nil)

func (v *tmuxTerminalView) Start(ctx context.Context, cols, rows int, out backendlink.FrameSink) (*backendlink.ViewHandle, error) {
	ptyFile, closePty, err := v.tmux.Attach(cols, rows)
	if err != nil {
		return nil, err
	}

	master := webttybridge.NewChannelMaster(webttybridge.FrameSink(out))
	// Tell webtty to base64-DECODE inbound Input frames. webtty defaults to a
	// no-op codec (NullCodec) and only switches to base64 upon a SetEncoding
	// ('4') control frame. The browser base64-encodes its keystrokes, so
	// without this the raw base64 text would be written straight into the pty
	// (garbled input). Feeding it here -- as the very first frame webtty reads,
	// before Start returns and any browser Input can arrive -- makes the
	// decoding correct with no client handshake or timing race.
	master.Feed([]byte("4base64"))
	slave := webttybridge.NewPtySlave(ptyFile, v.titleVars)

	bridge, err := webttybridge.New(master, slave, cols, rows)
	if err != nil {
		_ = closePty()
		return nil, err
	}

	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = bridge.Run(runCtx)
	}()

	var once sync.Once
	stop := func() {
		once.Do(func() {
			cancel()
			_ = master.Close()
			_ = closePty()
			<-done
		})
	}

	return &backendlink.ViewHandle{
		Feed: master.Feed,
		Resize: func(cols, rows int) error {
			return slave.ResizeTerminal(cols, rows)
		},
		Stop: stop,
	}, nil
}
