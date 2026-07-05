// Package backendlink implements cheesed's persistent, dial-out WebSocket
// connection to the backend orchestrator (contract §0, §5). cheesed always
// dials OUT to the backend so it works from behind NAT.
//
// One WS carries both control-plane JSON (WS TEXT) and raw webtty frames
// (WS BINARY): hello/heartbeat/status flow out; takeover/drive/resize/
// viewer flow in, demultiplexed purely by WS message type.
package backendlink

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// Driver is the subset of driver.Driver that the link needs to react to
// takeover and orchestrator-driven input.
type Driver interface {
	Pause()
	Resume()
	Drive(keys []string) error
}

// Backend is the seam callers depend on: a component that maintains the
// dial-out connection and relays control/terminal traffic until ctx is
// cancelled.
type Backend interface {
	Run(ctx context.Context) error
	// SendStatus reports a coarse phase upstream; assignable directly as a
	// driver.StatusFunc.
	SendStatus(phase, detail string)
}

var errNotConnected = errors.New("backendlink: not connected")

// Link is the default Backend implementation.
type Link struct {
	wsURL        string
	sessionToken string
	agentName    string
	cols, rows   int
	heartbeat    time.Duration

	driver Driver
	view   TerminalView

	dialer *websocket.Dialer

	mu         sync.Mutex
	conn       *websocket.Conn
	viewCols   int
	viewRows   int
	activeView *ViewHandle
	viewCancel context.CancelFunc

	writeMu sync.Mutex
}

var _ Backend = (*Link)(nil)

// New builds a Link. cols/rows seed the hello message and the default
// terminal-view size until a resize message arrives.
func New(wsURL, sessionToken, agentName string, cols, rows int, heartbeat time.Duration, driver Driver, view TerminalView) *Link {
	return &Link{
		wsURL:        wsURL,
		sessionToken: sessionToken,
		agentName:    agentName,
		cols:         cols,
		rows:         rows,
		heartbeat:    heartbeat,
		driver:       driver,
		view:         view,
		dialer:       websocket.DefaultDialer,
		viewCols:     cols,
		viewRows:     rows,
	}
}

// Run dials the backend, reconnecting with capped exponential backoff on
// failure/disconnect, until ctx is cancelled.
func (l *Link) Run(ctx context.Context) error {
	defer l.stopView()

	const (
		initialBackoff = time.Second
		maxBackoff     = 30 * time.Second
	)
	backoff := initialBackoff

	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		connected, err := l.runOnce(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if connected {
			backoff = initialBackoff
		} else if backoff < maxBackoff {
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
		}
		_ = err // connection errors are expected during outages; just retry

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
	}
}

// runOnce dials once and pumps messages until the connection drops or ctx
// is cancelled. connected reports whether the dial itself succeeded (used
// to decide whether to reset the backoff).
func (l *Link) runOnce(ctx context.Context) (connected bool, err error) {
	header := http.Header{}
	header.Set("X-Cheese-Session", l.sessionToken)

	conn, _, err := l.dialer.DialContext(ctx, l.wsURL, header)
	if err != nil {
		return false, fmt.Errorf("backendlink: dial: %w", err)
	}
	defer conn.Close()

	l.mu.Lock()
	l.conn = conn
	l.mu.Unlock()
	defer func() {
		l.mu.Lock()
		if l.conn == conn {
			l.conn = nil
		}
		l.mu.Unlock()
	}()

	if err := l.sendHello(); err != nil {
		return true, fmt.Errorf("backendlink: hello: %w", err)
	}

	hbCtx, hbCancel := context.WithCancel(ctx)
	defer hbCancel()
	go l.heartbeatLoop(hbCtx)

	for {
		msgType, data, err := conn.ReadMessage()
		if err != nil {
			return true, fmt.Errorf("backendlink: read: %w", err)
		}
		switch msgType {
		case websocket.TextMessage:
			l.handleControl(ctx, data)
		case websocket.BinaryMessage:
			l.handleBinary(data)
		}
	}
}

func (l *Link) heartbeatLoop(ctx context.Context) {
	ticker := time.NewTicker(l.heartbeat)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_ = l.writeText(heartbeatMsg{T: "heartbeat"})
		}
	}
}

func (l *Link) sendHello() error {
	return l.writeText(helloMsg{
		T:        "hello",
		Session:  l.sessionToken,
		Protocol: 1,
		Agent:    l.agentName,
		Cols:     l.cols,
		Rows:     l.rows,
	})
}

// SendStatus reports a coarse phase to the backend. It is assignable
// directly as a driver.StatusFunc.
func (l *Link) SendStatus(phase, detail string) {
	_ = l.writeText(statusMsg{T: "status", Phase: phase, Detail: detail})
}

func (l *Link) writeText(v interface{}) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return l.writeMessage(websocket.TextMessage, data)
}

func (l *Link) writeBinary(frame []byte) error {
	return l.writeMessage(websocket.BinaryMessage, frame)
}

func (l *Link) writeMessage(msgType int, data []byte) error {
	l.mu.Lock()
	conn := l.conn
	l.mu.Unlock()
	if conn == nil {
		return errNotConnected
	}
	l.writeMu.Lock()
	defer l.writeMu.Unlock()
	return conn.WriteMessage(msgType, data)
}
