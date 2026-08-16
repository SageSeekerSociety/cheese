// Package rendezvous delivers a turn's prompt to a running interactive Claude
// Code over the socket Claude Code itself binds for that purpose — no keystroke
// emulation, no screen reading, nothing that depends on how wide a pane is.
//
// Claude Code binds this socket when a session starts with three env vars set
// (CLAUDE_BG_BACKEND=daemon, CLAUDE_BG_RENDEZVOUS_SOCK, CLAUDE_BG_RV_AUTH). A
// `reply` frame written here is enqueued exactly where a keystroke lands, and
// the runtime stamps it `origin: {kind:"human"}` — which is the whole point:
// the platform relays what a PERSON said, and a peer-origin channel (the other
// socket, /tmp/cc-socks/<pid>.sock) makes the model treat it as another agent's
// request that must not count as its user's approval.
//
// Three properties this has and `tmux send-keys` never did:
//
//   - a write either reaches the session or returns an error, because a unix
//     socket is a real channel to a real process (send-keys "succeeds" into a
//     pane whose program is dead);
//   - the session pushes a heartbeat every 30s, so liveness is observed rather
//     than inferred from pixels;
//   - a refused frame comes back as `reply-rejected` / `auth-rejected` instead
//     of silence.
//
// The server side accepts ONE connection: a new one destroys the previous. So a
// Client holds a single long-lived connection and reconnects only when its own
// connection dies.
package rendezvous

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"time"
)

// Frame is the union of every field either side uses. Newline-delimited JSON.
type Frame struct {
	// Handshake (client -> session). Any frame carrying `role` is read as auth.
	Role string `json:"role,omitempty"`
	Auth string `json:"auth,omitempty"`

	Type string `json:"type,omitempty"`
	Text string `json:"text,omitempty"`

	// `state` frames carry the session's own view of what it is doing.
	Patch json.RawMessage `json:"patch,omitempty"`
	// `attacher-caps` frames describe the viewer.
	Caps json.RawMessage `json:"caps,omitempty"`
}

const (
	// Frame types the session sends us.
	TypeHeartbeat     = "heartbeat"
	TypeState         = "state"
	TypeAuthRejected  = "auth-rejected"
	TypeReplyRejected = "reply-rejected"
	TypeShuttingDown  = "shutting-down"

	// Frame types we send.
	TypeReply    = "reply"
	TypeRepaint  = "repaint"
	TypeShutdown = "shutdown"
)

// rejectWindow is how long Reply waits for a `reply-rejected` before reporting
// success. The protocol has no positive ack — a refusal is the only signal — so
// "delivered" means the write succeeded AND no refusal arrived promptly. The
// session decides on the frame the moment it parses the line, so this is a
// scheduling margin, not a round trip; it is deliberately far below any human's
// sense of latency and far above a local socket's turnaround.
const rejectWindow = 400 * time.Millisecond

// heartbeatGrace bounds how long we treat a silent connection as alive. The
// session beats every 30s, so two missed beats plus margin means the peer is
// gone even though TCP (a unix socket, here) never told us.
const heartbeatGrace = 75 * time.Second

// ErrClosed is returned by Reply once the client has been closed or the session
// went away. It is deliberately distinct from a rejection: one means "we could
// not speak", the other "the session refused what we said".
var ErrClosed = errors.New("rendezvous: connection closed")

// ErrRejected is returned when the session explicitly refused a frame.
var ErrRejected = errors.New("rendezvous: frame rejected by session")

// Options configure a Client. Zero values are sensible.
type Options struct {
	// WaitForSocket bounds how long Dial waits for the socket file to appear.
	// A freshly launched claude needs seconds (image pull, node boot, TUI
	// mount) before it binds, and treating that startup as a failure is what
	// made the old driver give up on a session that was merely still booting.
	WaitForSocket time.Duration
	// OnFrame receives every frame the session sends. It runs on the read
	// goroutine, so it must not block.
	OnFrame func(Frame)
	// Logf, if set, receives one line per notable event.
	Logf func(format string, args ...any)
}

// Client is one long-lived connection to one session's rendezvous socket.
type Client struct {
	path  string
	token string
	opts  Options

	mu      sync.Mutex
	conn    net.Conn
	writeMu sync.Mutex
	closed  bool

	// deliverMu serializes whole Reply calls, not just the write, so concurrent
	// callers queue instead of interleaving frames. It does not make a busy
	// session accept everything — see the contract on Reply.
	deliverMu sync.Mutex

	// rejects carries refusal frames to whichever Reply is in flight. Buffered
	// so a refusal that arrives with no Reply waiting is dropped rather than
	// wedging the read loop.
	rejects chan string

	lastBeat atomic[time.Time]
}

// atomic is a tiny mutex-guarded cell; sync/atomic has no time.Time.
type atomic[T any] struct {
	mu sync.Mutex
	v  T
}

func (a *atomic[T]) set(v T) { a.mu.Lock(); a.v = v; a.mu.Unlock() }
func (a *atomic[T]) get() T  { a.mu.Lock(); defer a.mu.Unlock(); return a.v }

// Dial waits for the socket to exist, connects, and completes the handshake.
//
// The handshake has no positive acknowledgement either — a good token is met
// with silence — so this reports success once the auth frame is written and no
// `auth-rejected` came back within the reject window.
func Dial(ctx context.Context, path, token string, opts Options) (*Client, error) {
	if path == "" {
		return nil, errors.New("rendezvous: empty socket path")
	}
	wait := opts.WaitForSocket
	if wait <= 0 {
		wait = 90 * time.Second
	}
	conn, err := dialWhenReady(ctx, path, wait)
	if err != nil {
		return nil, err
	}

	c := &Client{
		path:    path,
		token:   token,
		opts:    opts,
		conn:    conn,
		rejects: make(chan string, 8),
	}
	c.lastBeat.set(time.Now())
	go c.readLoop(conn)

	if token != "" {
		if err := c.write(Frame{Role: "attacher", Auth: token}); err != nil {
			conn.Close()
			return nil, fmt.Errorf("rendezvous: handshake: %w", err)
		}
		select {
		case kind := <-c.rejects:
			if kind == TypeAuthRejected {
				conn.Close()
				return nil, fmt.Errorf("%w: %s", ErrRejected, kind)
			}
		case <-time.After(rejectWindow):
		case <-ctx.Done():
			conn.Close()
			return nil, ctx.Err()
		}
	}
	c.logf("connected to %s", path)
	return c, nil
}

// dialWhenReady polls through both startup states: first the socket path does
// not exist, then bind may make it visible just before listen starts accepting.
// Returning at the first state transition races that tiny bind/listen window and
// turns a healthy late-starting session into a connection-refused failure.
func dialWhenReady(ctx context.Context, path string, within time.Duration) (net.Conn, error) {
	deadline := time.Now().Add(within)
	poll := time.NewTicker(100 * time.Millisecond)
	defer poll.Stop()
	seenSocket := false
	var lastDialErr error
	for {
		if fi, err := os.Stat(path); err == nil && fi.Mode()&os.ModeSocket != 0 {
			seenSocket = true
			remaining := time.Until(deadline)
			if remaining > 0 {
				conn, dialErr := net.DialTimeout("unix", path, min(5*time.Second, remaining))
				if dialErr == nil {
					return conn, nil
				}
				lastDialErr = dialErr
			}
		}
		if time.Now().After(deadline) {
			if !seenSocket {
				return nil, fmt.Errorf(
					"rendezvous: socket %s did not appear within %s", path, within,
				)
			}
			if lastDialErr == nil {
				return nil, fmt.Errorf(
					"rendezvous: socket %s was not ready within %s", path, within,
				)
			}
			return nil, fmt.Errorf(
				"rendezvous: socket %s did not accept connections within %s: %w",
				path,
				within,
				lastDialErr,
			)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-poll.C:
		}
	}
}

// Reply delivers one prompt as a human-origin turn.
//
// # What this guarantees, and what it does not
//
// Guaranteed: the frame reached the session's socket and was not refused. A
// failed write, or a `reply-rejected` coming back, is an error — never silence.
// That is the whole improvement over send-keys, where a pane whose program had
// died acked happily.
//
// NOT guaranteed: that the session turned it into a turn. This protocol has no
// positive acknowledgement, and a session that is mid-turn does not reliably
// queue what lands on top of it. Measured on a loaded CI runner: 8 prompts sent
// back-to-back produced 7 turns, 5 concurrent ones produced 4. The same tests
// pass every time on an idle laptop — which is exactly why this is written down
// instead of tuned away with a longer sleep, since a sleep that works on the
// fast machine is not a guarantee, only a wider window.
//
// Consumption is therefore confirmed one layer up, where evidence exists: the
// platform reads Claude Code's own hooks and re-sends a prompt whose hook never
// arrives. Turn openers are serialized. Mid-turn supplements may target a busy
// session, but they count as consumed only after the exact UserPromptSubmit hook;
// without it they remain pending and fall back to a queued turn. That is an
// at-least-once contract, not a stronger promise from this socket.
func (c *Client) Reply(text string) error {
	if text == "" {
		return errors.New("rendezvous: empty prompt")
	}
	c.deliverMu.Lock()
	defer c.deliverMu.Unlock()
	// Drain refusals left over from an earlier frame so this Reply cannot
	// inherit someone else's rejection — charging a delivered prompt with a
	// stale refusal would trigger a duplicate re-send.
	c.drainRejects()

	if err := c.write(Frame{Type: TypeReply, Text: text}); err != nil {
		return err
	}
	select {
	case kind := <-c.rejects:
		return fmt.Errorf("%w: %s", ErrRejected, kind)
	case <-time.After(rejectWindow):
		return nil
	}
}

func (c *Client) drainRejects() {
	for {
		select {
		case <-c.rejects:
		default:
			return
		}
	}
}

// Repaint asks the session to redraw — the one frame that needs no auth.
func (c *Client) Repaint() error { return c.write(Frame{Type: TypeRepaint}) }

// Shutdown asks the session to end itself.
func (c *Client) Shutdown() error { return c.write(Frame{Type: TypeShutdown}) }

// Alive reports whether the connection is open and has beaten recently enough.
func (c *Client) Alive() bool {
	c.mu.Lock()
	closed, conn := c.closed, c.conn
	c.mu.Unlock()
	if closed || conn == nil {
		return false
	}
	return time.Since(c.lastBeat.get()) < heartbeatGrace
}

// Close tears down the connection. Safe to call more than once.
func (c *Client) Close() error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil
	}
	c.closed = true
	conn := c.conn
	c.conn = nil
	c.mu.Unlock()
	if conn != nil {
		return conn.Close()
	}
	return nil
}

func (c *Client) write(f Frame) error {
	c.mu.Lock()
	closed, conn := c.closed, c.conn
	c.mu.Unlock()
	if closed || conn == nil {
		return ErrClosed
	}
	line, err := json.Marshal(f)
	if err != nil {
		return fmt.Errorf("rendezvous: marshal: %w", err)
	}
	line = append(line, '\n')

	// One frame per write, serialized: two concurrent writers could interleave
	// halves of two JSON objects into one line and the session would drop both.
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	if err := conn.SetWriteDeadline(time.Now().Add(10 * time.Second)); err != nil {
		return fmt.Errorf("rendezvous: set deadline: %w", err)
	}
	if _, err := conn.Write(line); err != nil {
		return fmt.Errorf("rendezvous: write: %w", err)
	}
	return nil
}

func (c *Client) readLoop(conn net.Conn) {
	defer func() {
		c.mu.Lock()
		if c.conn == conn {
			c.conn = nil
			c.closed = true
		}
		c.mu.Unlock()
		conn.Close()
	}()

	sc := bufio.NewScanner(conn)
	// Frames are small; the default 64KB is plenty, but a state patch could in
	// principle be larger, and a scanner that stops on a long line would look
	// exactly like a dead session.
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var f Frame
		if err := json.Unmarshal(line, &f); err != nil {
			c.logf("undecodable frame (%d bytes): %v", len(line), err)
			continue
		}
		switch f.Type {
		case TypeHeartbeat:
			c.lastBeat.set(time.Now())
		case TypeAuthRejected, TypeReplyRejected:
			c.lastBeat.set(time.Now())
			select {
			case c.rejects <- f.Type:
			default: // nobody waiting; the log line below is the record
			}
			c.logf("session refused a frame: %s", f.Type)
		case TypeShuttingDown:
			c.logf("session is shutting down")
		default:
			c.lastBeat.set(time.Now())
		}
		if c.opts.OnFrame != nil {
			c.opts.OnFrame(f)
		}
	}
	if err := sc.Err(); err != nil {
		c.logf("read loop ended: %v", err)
	}
}

func (c *Client) logf(format string, args ...any) {
	if c.opts.Logf != nil {
		c.opts.Logf(format, args...)
	}
}
