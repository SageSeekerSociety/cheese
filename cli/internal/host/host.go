// Package host is the composition root of `cheese run`: it dials the server and
// lets the server open any number of screens on this machine. Each screen is a
// program in a terminal, and the server reaches it three ways:
//
//   - as a raw byte stream to and from the terminal (what a browser viewer
//     rides),
//   - through the screen's own rendezvous socket, where a prompt is enqueued as
//     human-origin input without passing through the terminal at all,
//   - and by staging files into the screen's workspace.
//
// The host wires those together and ascribes no meaning to any of it; all
// behavior lives in the server.
package host

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/link"
	"github.com/SageSeekerSociety/cheese/cli/internal/rendezvous"
	"github.com/SageSeekerSociety/cheese/cli/internal/state"
	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
	"github.com/SageSeekerSociety/cheese/cli/internal/update"
)

// LoadConfig reads this machine's config from path.
func LoadConfig(path string) (*config.Config, error) { return config.Load(path) }

// Host owns the connection, the tmux manager, and the live screens.
type Host struct {
	conn    *link.Conn
	tm      *terminal.Manager
	cfgPath string // for the shared screen-count state file ("" disables)
	base    string // server origin, for self-update downloads

	ctx      context.Context
	mu       sync.Mutex
	sessions map[string]*sess

	execMu sync.Mutex
	execs  map[string]context.CancelFunc // in-flight exec id -> cancel

	updating atomic.Bool // guards against concurrent / re-entrant self-updates
}

type sess struct {
	term     *terminal.Session
	client   *terminal.Client // a real tmux client (pty) while a viewer is attached
	lastCols int
	lastRows int

	// Prompt delivery goes through the session's own rendezvous socket when the
	// launcher armed one (CLAUDE_BG_RENDEZVOUS_SOCK / CLAUDE_BG_RV_AUTH in the
	// screen env). That path replaces typing into the terminal entirely: the
	// text is enqueued by Claude Code as human-origin input, so nothing about
	// delivery depends on pane width, TUI state, or a screen scrape.
	// rvTokenFile is read lazily, not at create time: the launcher writes it
	// while claude boots, which is strictly after the screen is spawned.
	rvPath      string
	rvTokenFile string
	workDir     string
	rvMu        sync.Mutex
	rv          *rendezvous.Client
}

// New builds a Host from cfg. cfgPath locates the shared state file that lets
// CLI commands see how many screens are live ("" disables that).
func New(cfg *config.Config, cfgPath string) (*Host, error) {
	ctrlURL, err := cfg.ControlURL()
	if err != nil {
		return nil, err
	}
	tm, err := terminal.NewManager()
	if err != nil {
		return nil, err
	}
	// Which binary this is, for the server's staleness check. Both are
	// best-effort: a connector that cannot hash itself or does not know its own
	// platform announces nothing for that field, and the server declines to act
	// on a half-answer rather than re-exec this machine on every reconnect.
	build, _ := update.SelfDigest()
	target, _ := update.PlatformDir()
	return &Host{
		conn:     link.New(ctrlURL, cfg.Token, build, target),
		tm:       tm,
		cfgPath:  cfgPath,
		base:     cfg.Base,
		sessions: map[string]*sess{},
		execs:    map[string]context.CancelFunc{},
	}, nil
}

// Run connects and serves screens until ctx is cancelled, then LETS GO of them:
// it releases what this process owns (viewer ptys, rendezvous clients) and
// leaves every tmux session running.
//
// Stopping the connector is not a decision to end anybody's work. The service
// manager stops this process to restart it, to apply an update, on a reboot —
// and a turn mid-flight on this machine has nothing to do with any of that. So
// the exit path releases and the sessions live on; the next run re-adopts them
// (createSession's HasSession branch), the drainer keeps retrying the hooks it
// spooled, and the viewer reattaches to the pane it left. Ending a session is a
// separate, explicit act: the server closing a screen, or the operator running
// `cheese link disconnect` / `cheese uninstall`, which tear the server down.
func (h *Host) Run(ctx context.Context) error {
	h.ctx = ctx
	h.publishState()
	defer h.clearState()
	defer h.releaseAll()

	// A manual `cheese update` signals the running service with SIGUSR2 so the
	// update happens INSIDE this process (which then hands off via syscall.Exec,
	// preserving the private tmux + its tasks). Handle it here for the lifetime of
	// the run. Note: a successful update never returns from performUpdate — it
	// replaces the process image — so none of the deferred teardown above runs.
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGUSR2)
	defer signal.Stop(sig)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-sig:
				go h.performUpdate()
			}
		}
	}()

	return h.conn.Run(ctx, h.onMsg)
}

// performUpdate updates the `cheese` binary in place and hands this process off to
// it. It runs inside the live service process: download + verify + atomic
// replace, then syscall.Exec into the new binary — which REPLACES the process
// image, so even the release path below never runs; the new binary reconnects
// and re-adopts the surviving screens. Any failure keeps the current process
// running unchanged (a failed update must never kill live tasks).
func (h *Host) performUpdate() {
	if !h.updating.CompareAndSwap(false, true) {
		return // an update is already in flight
	}
	defer h.updating.Store(false)

	if h.base == "" {
		fmt.Fprintln(os.Stderr, "cheese: update requested but no server base is configured")
		return
	}
	self, err := update.SelfPath()
	if err != nil {
		fmt.Fprintf(os.Stderr, "cheese: update: cannot locate self: %v\n", err)
		return
	}
	tmp, err := update.Fetch(h.ctx, h.base)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cheese: update aborted (kept running current build): %v\n", err)
		return
	}
	if err := update.Replace(tmp, self); err != nil {
		fmt.Fprintf(os.Stderr, "cheese: update aborted (kept running current build): %v\n", err)
		return
	}
	// Detach any live viewer pty clients (but NOT the tmux sessions) before the exec.
	// syscall.Exec skips the deferred releaseAll, so an attached viewer's tmux client
	// (a child process) would otherwise survive as an ORPHAN still attached to the
	// session — and with `window-size latest` it fights the fresh viewer the new
	// binary attaches, leaving 现场 garbled/unopenable. Closing the client here only
	// tears down the viewer relay; the program/task in the tmux session lives on and
	// the new binary re-adopts it, then a re-subscribe attaches a clean single viewer.
	h.closeViewerClients()
	fmt.Fprintln(os.Stderr, "cheese: binary updated in place; handing off to the new build (tasks preserved)…")
	// syscall.Exec replaces the process image, so the viewer relays this process
	// owns are the only thing that has to be let go by hand; the private tmux and
	// every hosted task survive as they do across an ordinary stop, and the new
	// image reconnects and re-adopts them. If exec fails we deliberately do NOT exit —
	// the tasks must live on; the already-replaced binary applies on next restart.
	if err := syscall.Exec(self, os.Args, os.Environ()); err != nil {
		fmt.Fprintf(os.Stderr, "cheese: exec into new binary failed (applies on next restart): %v\n", err)
	}
}

func (h *Host) publishState() {
	if h.cfgPath == "" {
		return
	}
	h.mu.Lock()
	n := len(h.sessions)
	h.mu.Unlock()
	state.Write(h.cfgPath, n)
}

func (h *Host) clearState() {
	if h.cfgPath != "" {
		state.Clear(h.cfgPath)
	}
}

func (h *Host) onMsg(m link.Msg) {
	switch m.T {
	case "welcome":
		if m.V != 0 && m.V != link.Version {
			fmt.Fprintf(os.Stderr, "cheese: protocol version mismatch (server %d, client %d) — update cheese if things misbehave\n", m.V, link.Version)
		}
	case "session.create":
		h.createSession(m)
	case "session.close":
		h.closeSession(m.Sid)
	case "rpc.call": // the server asks this screen to do something
		if s := h.session(m.Sid); s != nil {
			go h.serveCall(m, s)
		}
	case "file.put":
		if s := h.session(m.Sid); s != nil {
			go h.putFile(m, s)
		} else {
			_ = h.conn.Send(link.Msg{T: "file.result", Sid: m.Sid, ID: m.ID, Error: "unknown screen"})
		}
	case "screen.subscribe": // attach a real tmux client sized to the viewer
		h.subscribeScreen(m.Sid, m.Cols, m.Rows)
	case "screen.unsubscribe":
		h.unsubscribeScreen(m.Sid)
	case "screen.input": // raw viewer keystrokes -> the client's pty
		if s := h.session(m.Sid); s != nil && s.client != nil {
			if b, err := base64.StdEncoding.DecodeString(m.Data); err == nil {
				_ = s.client.Write(b)
			}
		}
	case "exec": // run a one-shot command on this machine and return its output
		go h.runExec(m)
	case "exec.cancel": // stop an in-flight exec (e.g. the caller's timeout fired)
		h.cancelExec(m.ID)
	case "update": // server-pushed forced update: update in place and re-exec
		go h.performUpdate()
	case "screen.resize":
		if s := h.session(m.Sid); s != nil {
			// Viewers re-send their size continuously (and on a timer) to keep the
			// real terminal matched to what they render. Resize only on an actual
			// change, so same-size pings don't make the program repaint.
			h.mu.Lock()
			changed := m.Cols != s.lastCols || m.Rows != s.lastRows
			s.lastCols, s.lastRows = m.Cols, m.Rows
			client := s.client
			h.mu.Unlock()
			if changed && client != nil {
				_ = client.Resize(m.Cols, m.Rows)
			}
		}
	}
}

func (h *Host) session(sid string) *sess {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.sessions[sid]
}

func (h *Host) createSession(m link.Msg) {
	if h.session(m.Sid) != nil {
		// Same-process reconnect: the screen is already live locally, and every
		// way of reaching it is per-message, so there is nothing to re-establish.
		return
	}
	// The server owns the screen's identity: it hands down an opaque token in
	// m.Screen, which the host injects as CHEESE_SCREEN so any process the screen
	// spawns can prove which screen it belongs to when it calls back.
	env := make([]string, 0, len(m.Env)+1)
	if m.Screen != "" {
		env = append(env, "CHEESE_SCREEN="+m.Screen)
	}
	for k, v := range m.Env {
		env = append(env, k+"="+v)
	}

	// If a tmux session for this sid already exists (it survived a `cheese update`
	// re-exec or a server restart), ADOPT it: re-attach to the still-running
	// program instead of spawning a new session. Otherwise spawn a fresh tmux
	// session + program as usual. The server sets m.Adopt on the re-provision
	// path; HasSession is the ground truth we act on.
	var term *terminal.Session
	if h.tm.HasSession(m.Sid) {
		term = h.tm.Adopt(m.Sid)
	} else {
		var err error
		term, err = h.tm.Spawn(m.Sid, m.Command, env, m.Cols, m.Rows)
		if err != nil {
			_ = h.conn.Send(link.Msg{T: "session.error", Sid: m.Sid, Error: err.Error()})
			return
		}
	}
	h.mu.Lock()
	h.sessions[m.Sid] = &sess{
		term:        term,
		rvPath:      m.Env[envRvSock],
		rvTokenFile: m.Env[envRvTokenFile],
		workDir:     m.Env["CHEESE_WORK"],
	}
	h.mu.Unlock()

	_ = h.conn.Send(link.Msg{T: "session.ready", Sid: m.Sid})
	h.publishState()
}

func (h *Host) subscribeScreen(sid string, cols, rows int) {
	s := h.session(sid)
	if s == nil || s.client != nil {
		return
	}
	client, err := s.term.Attach(cols, rows, func(b []byte) {
		_ = h.conn.Send(link.Msg{T: "screen.data", Sid: sid,
			Data: base64.StdEncoding.EncodeToString(b)})
	})
	if err != nil {
		_ = h.conn.Send(link.Msg{T: "session.error", Sid: sid, Error: err.Error()})
		return
	}
	h.mu.Lock()
	s.client = client
	s.lastCols, s.lastRows = cols, rows
	h.mu.Unlock()
}

func (h *Host) unsubscribeScreen(sid string) {
	h.mu.Lock()
	s := h.sessions[sid]
	var client *terminal.Client
	if s != nil {
		client, s.client = s.client, nil
		s.lastCols, s.lastRows = 0, 0
	}
	h.mu.Unlock()
	if client != nil {
		client.Close()
	}
}

func (h *Host) closeSession(sid string) {
	h.mu.Lock()
	s := h.sessions[sid]
	delete(h.sessions, sid)
	h.mu.Unlock()
	h.teardown(s)
	h.publishState()
}

// closeViewerClients detaches every live viewer pty (s.client) WITHOUT touching the
// tmux sessions/tasks — used before a self-update exec so no viewer client orphans.
func (h *Host) closeViewerClients() {
	h.mu.Lock()
	clients := make([]*terminal.Client, 0, len(h.sessions))
	for _, s := range h.sessions {
		if s.client != nil {
			clients = append(clients, s.client)
			s.client, s.lastCols, s.lastRows = nil, 0, 0
		}
	}
	h.mu.Unlock()
	for _, c := range clients {
		c.Close()
	}
}

// releaseAll lets go of every screen without ending any of it: the tmux sessions
// (and the tmux server) keep running, so a stop/restart of this process is not a
// decision about anybody's in-flight turn.
func (h *Host) releaseAll() {
	h.mu.Lock()
	all := h.sessions
	h.sessions = map[string]*sess{}
	h.mu.Unlock()
	for _, s := range all {
		h.release(s)
	}
}

// release drops what this PROCESS owns for a screen — the viewer's pty client
// and the rendezvous connection. Nothing here outlives the process anyway, and
// none of it is the screen's work.
func (h *Host) release(s *sess) {
	if s == nil {
		return
	}
	if s.client != nil {
		s.client.Close()
	}
	s.rvMu.Lock()
	if s.rv != nil {
		s.rv.Close()
		s.rv = nil
	}
	s.rvMu.Unlock()
}

// teardown ends a screen for good: release, then kill the tmux session with the
// program in it. Only for a close the SERVER asked for.
func (h *Host) teardown(s *sess) {
	if s == nil {
		return
	}
	h.release(s)
	_ = s.term.Close()
}

// The screen-env keys the launcher and this host agree on. The launcher derives
// Claude Code's own three variables from them and writes the token file; the
// host reads the same two to find the socket and its token. Keeping the token
// in a FILE rather than the env is what makes an adopted screen work: a reused
// `claude` keeps the token it booted with, so a fresh env value would not match
// — the file is the single copy both sides read.
const (
	envRvSock      = "CHEESE_RV_SOCK"
	envRvTokenFile = "CHEESE_RV_TOKEN_FILE"
	promptCall     = "prompt"
)

// rvDialWindow bounds how long we wait for a booting claude to bind its socket.
// A cold screen (image pull, node start, TUI mount) has been measured well over
// a minute; giving up early is what made the old driver abandon a prompt while
// the session was merely still starting.
const rvDialWindow = 120 * time.Second

const maxScreenFileBytes = 10 << 20

// putFile stages an uploaded image before its @path is submitted over rendezvous.
// The acknowledgement is the ordering boundary: the prompt cannot race ahead of
// the bytes on a remote device.
func (h *Host) putFile(m link.Msg, s *sess) {
	reply := func(value any, errStr string) {
		_ = h.conn.Send(link.Msg{T: "file.result", Sid: m.Sid, ID: m.ID, Value: value, Error: errStr})
	}
	workDir, err := resolveScreenWorkDir(s.workDir)
	if err == nil {
		err = writeScreenFile(workDir, m.Path, m.Data)
	}
	if err != nil {
		reply(nil, fmt.Sprintf("file.put: %v", err))
		return
	}
	reply(map[string]any{"ok": true, "path": m.Path}, "")
}

func resolveScreenWorkDir(workDir string) (string, error) {
	if workDir == "" {
		return "", fmt.Errorf("screen has no work directory")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	if workDir == "$HOME" {
		workDir = home
	} else if strings.HasPrefix(workDir, "$HOME/") {
		workDir = filepath.Join(home, strings.TrimPrefix(workDir, "$HOME/"))
	}
	if !filepath.IsAbs(workDir) {
		return "", fmt.Errorf("work directory is not absolute")
	}
	return filepath.Clean(workDir), nil
}

func writeScreenFile(workDir, wirePath, encoded string) error {
	clean := path.Clean(wirePath)
	if path.IsAbs(clean) || clean == "." || clean == "uploads" ||
		!strings.HasPrefix(clean, "uploads/") || strings.HasPrefix(clean, "../") {
		return fmt.Errorf("path must be a file under uploads/")
	}
	if len(encoded) > base64.StdEncoding.EncodedLen(maxScreenFileBytes) {
		return fmt.Errorf("file exceeds %d bytes", maxScreenFileBytes)
	}
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return fmt.Errorf("invalid base64: %w", err)
	}
	if len(raw) == 0 || len(raw) > maxScreenFileBytes {
		return fmt.Errorf("invalid file size %d", len(raw))
	}
	root, err := filepath.Abs(workDir)
	if err != nil {
		return err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return fmt.Errorf("resolve work directory: %w", err)
	}
	target := filepath.Join(root, filepath.FromSlash(clean))
	parent := filepath.Dir(target)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	realParent, err := filepath.EvalSymlinks(parent)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(root, realParent)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return fmt.Errorf("upload path escapes screen workspace")
	}
	tmp, err := os.CreateTemp(realParent, ".cheese-upload-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	err = tmp.Chmod(0o644)
	if err == nil {
		_, err = tmp.Write(raw)
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmpPath, filepath.Join(realParent, filepath.Base(target)))
}

// deliverPrompt hands one turn's prompt to the session over its rendezvous
// socket and answers the server's rpc.call with the outcome. Failure here is
// REPORTED, never retried into the void: the whole point of leaving send-keys
// behind is that a prompt either lands or says why not.
func (h *Host) deliverPrompt(m link.Msg, s *sess) {
	text := ""
	if len(m.Args) > 0 {
		if str, ok := m.Args[0].(string); ok {
			text = str
		}
	}
	reply := func(value any, errStr string) {
		_ = h.conn.Send(link.Msg{T: "rpc.result", Sid: m.Sid, ID: m.ID, Value: value, Error: errStr})
	}
	if text == "" {
		reply(nil, "rendezvous: empty prompt")
		return
	}

	c, err := h.rendezvousClient(s)
	if err != nil {
		reply(nil, err.Error())
		return
	}
	err = c.Reply(text)
	if err == nil {
		reply(map[string]any{"ok": true, "ready": true, "transport": "rendezvous"}, "")
		return
	}
	// One reconnect-and-retry. A session that has been idle can have dropped
	// our connection (or been re-attached by another client), and that failure
	// mode is indistinguishable from a dead session until we try again. A
	// rejected or unwritable frame never reached the queue, so a retry cannot
	// duplicate a delivered prompt.
	h.dropRendezvous(s)
	if c2, err2 := h.rendezvousClient(s); err2 == nil {
		if err3 := c2.Reply(text); err3 == nil {
			reply(map[string]any{"ok": true, "ready": true, "transport": "rendezvous", "retried": true}, "")
			return
		} else {
			err = err3
		}
	}
	reply(nil, fmt.Sprintf("rendezvous delivery failed: %v", err))
}

// rendezvousClient returns a live client for the screen, dialling on first use.
func (h *Host) rendezvousClient(s *sess) (*rendezvous.Client, error) {
	s.rvMu.Lock()
	defer s.rvMu.Unlock()
	if s.rv != nil && s.rv.Alive() {
		return s.rv, nil
	}
	if s.rv != nil {
		s.rv.Close()
		s.rv = nil
	}
	token, err := readRvToken(s.rvTokenFile)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(h.ctx, rvDialWindow+15*time.Second)
	defer cancel()
	c, err := rendezvous.Dial(ctx, s.rvPath, token, rendezvous.Options{
		WaitForSocket: rvDialWindow,
		Logf: func(format string, args ...any) {
			fmt.Fprintf(os.Stderr, "cheese: rendezvous: "+format+"\n", args...)
		},
	})
	if err != nil {
		return nil, err
	}
	s.rv = c
	return c, nil
}

func (h *Host) dropRendezvous(s *sess) {
	s.rvMu.Lock()
	defer s.rvMu.Unlock()
	if s.rv != nil {
		s.rv.Close()
		s.rv = nil
	}
}

// rvTokenWait bounds the wait below. A var, not a const, so a test does not
// have to spend it.
var rvTokenWait = 20 * time.Second

// serveCall answers one server->screen call. `prompt` is the only thing a screen
// can be asked to do, and it goes over the rendezvous socket the launcher armed.
// Anything else — a call this build does not know, or a prompt for a screen with
// no socket — is answered with an error rather than dropped: a call the server
// believes it made and this side silently ignored is the exact failure shape
// this delivery path exists to end.
func (h *Host) serveCall(m link.Msg, s *sess) {
	if m.Name != promptCall {
		_ = h.conn.Send(link.Msg{T: "rpc.result", Sid: m.Sid, ID: m.ID,
			Error: fmt.Sprintf("unknown call %q", m.Name)})
		return
	}
	if s.rvPath == "" {
		_ = h.conn.Send(link.Msg{T: "rpc.result", Sid: m.Sid, ID: m.ID,
			Error: "rendezvous: this screen has no socket (" + envRvSock + " was not in its env)"})
		return
	}
	h.deliverPrompt(m, s)
}

// readRvToken reads the launcher-written token. It waits briefly: the file is
// written on the launcher's way to exec'ing claude, so a prompt that races a
// cold screen can arrive a moment before it exists.
func readRvToken(path string) (string, error) {
	if path == "" {
		return "", fmt.Errorf("rendezvous: no token file configured (%s)", envRvTokenFile)
	}
	deadline := time.Now().Add(rvTokenWait)
	for {
		b, err := os.ReadFile(path)
		if err == nil {
			if tok := strings.TrimSpace(string(b)); tok != "" {
				return tok, nil
			}
		}
		if time.Now().After(deadline) {
			return "", fmt.Errorf("rendezvous: token file %s never appeared", path)
		}
		time.Sleep(200 * time.Millisecond)
	}
}

// execMaxOut caps each of stdout/stderr so a runaway command can't exhaust memory.
const execMaxOut = 1 << 20 // 1 MiB per stream

// runExec runs a one-shot command on this machine and returns stdout/stderr/exit
// to the server. This is a generic device capability, independent of screens —
// for setup, health checks, and other fire-and-forget device-side work. The
// caller may bound it (Timeout), feed it input (Stdin) and cancel it mid-run
// (an exec.cancel with the same ID); output beyond execMaxOut is dropped.
func (h *Host) runExec(m link.Msg) {
	if len(m.Command) == 0 {
		_ = h.conn.Send(link.Msg{T: "exec.result", ID: m.ID, Stderr: "empty command", Exit: -1})
		return
	}
	timeout := time.Duration(m.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	ctx, cancel := context.WithTimeout(h.ctx, timeout)
	defer cancel()
	if m.ID != "" { // register so an exec.cancel can stop us
		h.execMu.Lock()
		h.execs[m.ID] = cancel
		h.execMu.Unlock()
		defer func() {
			h.execMu.Lock()
			delete(h.execs, m.ID)
			h.execMu.Unlock()
		}()
	}

	cmd := exec.CommandContext(ctx, m.Command[0], m.Command[1:]...)
	if m.Cwd != "" {
		cmd.Dir = m.Cwd
	}
	env := os.Environ()
	for k, v := range m.Env {
		env = append(env, k+"="+v)
	}
	cmd.Env = env
	if m.Stdin != "" {
		cmd.Stdin = strings.NewReader(m.Stdin)
	}
	stdout := &cappedBuffer{limit: execMaxOut}
	stderr := &cappedBuffer{limit: execMaxOut}
	cmd.Stdout, cmd.Stderr = stdout, stderr

	exit := 0
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			exit = ee.ExitCode()
		} else {
			exit = -1
			if stderr.Len() == 0 {
				stderr.buf.WriteString(err.Error())
			}
		}
	}
	_ = h.conn.Send(link.Msg{T: "exec.result", ID: m.ID,
		Stdout: stdout.String(), Stderr: stderr.String(), Exit: exit,
		Truncated: stdout.truncated || stderr.truncated})
}

func (h *Host) cancelExec(id string) {
	h.execMu.Lock()
	cancel := h.execs[id]
	h.execMu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// cappedBuffer accumulates up to limit bytes and silently drops the rest, so a
// runaway command's output can never blow up memory. It always reports a full
// write, so the child process is never blocked by a full pipe.
type cappedBuffer struct {
	buf       bytes.Buffer
	limit     int
	truncated bool
}

func (c *cappedBuffer) Write(p []byte) (int, error) {
	if room := c.limit - c.buf.Len(); room > 0 {
		if len(p) > room {
			c.buf.Write(p[:room])
			c.truncated = true
		} else {
			c.buf.Write(p)
		}
	} else if len(p) > 0 {
		c.truncated = true
	}
	return len(p), nil
}

func (c *cappedBuffer) String() string { return c.buf.String() }
func (c *cappedBuffer) Len() int       { return c.buf.Len() }
