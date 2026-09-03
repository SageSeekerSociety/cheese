// Package terminal hosts programs in a private tmux server and exposes each as
// a read/write surface with change notifications. It has no idea what runs
// inside — it spawns whatever argv it is handed, snapshots the screen, writes
// bytes, and reports when the screen changed.
package terminal

import (
	"bytes"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/creack/pty"
)

// Manager owns one private tmux server (a single unix socket). Every hosted
// program is a tmux session inside it, keyed by an opaque name.
type Manager struct {
	bin  string
	sock string
	conf string
}

// wellKnownTmuxDirs are the places tmux actually gets installed, searched after
// PATH fails.
//
// PATH is not enough for the way this binary usually RUNS. A macOS LaunchAgent
// (and a systemd unit, for the same reason) inherits the service manager's
// environment, not a login shell's: on macOS that PATH is
// /usr/bin:/bin:/usr/sbin:/sbin, which contains no tmux under any package
// manager. The connector then exits at startup, the machine simply never comes
// online, and the only evidence is one line in the plist's stderr file — while
// running the same binary by hand from a shell works perfectly.
//
// $HOME is expanded per entry rather than resolved once, so an empty HOME (also
// possible under a service manager) skips those entries instead of searching /.
var wellKnownTmuxDirs = []string{
	"/opt/homebrew/bin",                // homebrew, apple silicon
	"/usr/local/bin",                   // homebrew, intel — and the usual make-install target
	"/opt/local/bin",                   // macports
	"$HOME/.nix-profile/bin",           // nix, single-user
	"/etc/profiles/per-user/$USER/bin", // nix-darwin / home-manager
	"/run/current-system/sw/bin",       // nixos
	"/home/linuxbrew/.linuxbrew/bin",   // linuxbrew
}

// findTmux prefers a private tmux owned by this installation over whatever the
// host system happens to have: $CHEESE_TMUX, then <user-config>/cheese/bin/tmux
// (placed there by an installer), then PATH, then the well-known install dirs
// above. This keeps the CLI self-contained — a machine without tmux works once
// the installer drops one in, and a machine with a quirky system tmux is never
// at its mercy.
//
// The error names every place that was searched: the previous message stated
// one remedy and no evidence, which turned "which tmux is it not seeing?" into
// a support round-trip.
func findTmux() (string, error) {
	var searched []string

	if p := os.Getenv("CHEESE_TMUX"); p != "" {
		searched = append(searched, p+" ($CHEESE_TMUX)")
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	if base, err := os.UserConfigDir(); err == nil {
		p := filepath.Join(base, "cheese", "bin", "tmux")
		searched = append(searched, p)
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	if p, err := exec.LookPath("tmux"); err == nil {
		return p, nil
	}
	searched = append(searched, "PATH="+os.Getenv("PATH"))

	for _, dir := range wellKnownTmuxDirs {
		expanded := os.ExpandEnv(dir)
		// An unset HOME/USER leaves the variable empty, which would turn
		// "$HOME/.nix-profile/bin" into "/.nix-profile/bin" — a real path that
		// nobody installs into. Skip rather than search it.
		if expanded == dir && strings.Contains(dir, "$") {
			continue
		}
		if strings.HasPrefix(expanded, "/.") || strings.Contains(expanded, "//") {
			continue
		}
		p := filepath.Join(expanded, "tmux")
		searched = append(searched, p)
		if fi, err := os.Stat(p); err == nil && !fi.IsDir() {
			return p, nil
		}
	}

	return "", fmt.Errorf(
		"terminal: no tmux found (install one, or place a private copy at "+
			"<config>/cheese/bin/tmux, or set $CHEESE_TMUX). Looked in: %s",
		strings.Join(searched, ", "))
}

// NewManager locates tmux and provisions a private, short-path socket dir. It
// writes a tiny config that keeps a pane after its program exits: read when the
// server first starts (via -f), so it applies before any command can run and a
// program that dies instantly still leaves its output and a dead marker on
// screen instead of tearing the server down.
func NewManager() (*Manager, error) {
	bin, err := findTmux()
	if err != nil {
		return nil, err
	}
	// A STABLE per-user runtime dir — NOT a fresh MkdirTemp each start. The tmux
	// server daemonizes and outlives the cheese process; a restarted or self-updated
	// (syscall.Exec) cheese must reconnect to the SAME socket to find and re-adopt the
	// surviving sessions. A random dir per process would strand them on an orphan
	// socket (which is exactly what broke in-place update before this).
	dir := filepath.Join(os.TempDir(), fmt.Sprintf("cheese-%d", os.Getuid()))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("terminal: runtime dir: %w", err)
	}
	conf := filepath.Join(dir, "tmux.conf")
	// `remain-on-exit on`: keep a pane after its program exits, so a program
	// that dies instantly still leaves its output and a dead marker on screen
	// instead of tearing the server down.
	//
	// `exit-empty off`: keep the SERVER after its last session ends. tmux
	// otherwise exits an empty server, and then the next `new-session` forks a
	// fresh one from whoever asked — which on Linux puts it back in the
	// connector's cgroup and undoes EnsureServer for every session after the
	// first quiet moment. Measured: without this, a server started inside a
	// scope is gone before the first spawn and the spawn re-forks it here.
	if err := os.WriteFile(
		conf, []byte("set -g remain-on-exit on\nset -g exit-empty off\n"), 0o600,
	); err != nil {
		return nil, fmt.Errorf("terminal: write config: %w", err)
	}
	return &Manager{bin: bin, sock: filepath.Join(dir, "t.sock"), conf: conf}, nil
}

func (m *Manager) tmux(args ...string) *exec.Cmd {
	full := append([]string{"-S", m.sock, "-f", m.conf}, args...)
	cmd := exec.Command(m.bin, full...)
	cmd.Env = append(os.Environ(), "LC_ALL=C.UTF-8", "LANG=C.UTF-8")
	return cmd
}

// serverUp reports whether a tmux server is already listening on our socket.
//
// Asked of the socket rather than of tmux, because tmux answers "no server
// running" and "no such session" with the same exit code, and a stale socket
// file left behind by a dead server looks identical to a live one by name. A
// connect either reaches a process or it does not.
func (m *Manager) serverUp() bool {
	c, err := net.Dial("unix", m.sock)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

// EnsureServer brings the tmux server up before anything else asks it for a
// session, and on Linux puts it in a systemd scope of its own.
//
// The server is otherwise started implicitly: the first tmux command that needs
// one forks it. Forked from this process, it lands in THIS process's cgroup —
// and systemd manages a unit by its cgroup, so an operation addressed at the
// unit is addressed at every session too. `KillMode=process` exempts them from
// one such operation, `stop`, and from that one only: `systemctl kill
// --kill-whom=all` still reaches them, and a cgroup-level resource limit on the
// unit still counts every `claude` inside them against the connector's budget.
//
// A scope is how systemd is asked to put processes in a group of their own, and
// it is the only way out, because a process cannot leave its own cgroup —
// root or systemd has to move it. Measured on systemd 252: the server
// daemonizes to PPID 1 and stays in the scope, the scope stays `active
// (running)` with only that daemonized server left in it, and it goes
// `inactive` by itself once the server exits. So this adds no teardown path.
//
// macOS needs no equivalent: tmux daemonizes into a process group of its own
// and launchd tears down only the job's own group, so the sessions already
// survive `launchctl bootout`.
//
// A non-empty `degraded` means the server had to be started the old way (no
// systemd-run, or the scope was refused). The sessions work either way; what is
// lost is everything `KillMode=process` does not cover, so the caller is
// expected to say so rather than let it pass silently.
func (m *Manager) EnsureServer() (degraded string, err error) {
	if m.serverUp() {
		// Already running: a restart or a self-update found the server it left
		// behind. Its cgroup was decided when it started and nothing short of
		// restarting it can move it, so there is nothing to do and nothing to
		// report.
		return "", nil
	}
	if runtime.GOOS != "linux" {
		return "", m.tmux("start-server").Run()
	}
	// --collect so a scope that fails is reaped instead of lingering as a
	// failed unit that the next start would then collide with.
	scope := exec.Command("systemd-run",
		"--user", "--scope", "--quiet", "--collect",
		"--unit", fmt.Sprintf("cheese-tmux-%d", os.Getuid()),
		"--", m.bin, "-S", m.sock, "-f", m.conf, "start-server")
	scope.Env = append(os.Environ(), "LC_ALL=C.UTF-8", "LANG=C.UTF-8")
	var stderr bytes.Buffer
	scope.Stderr = &stderr
	if err := scope.Run(); err == nil {
		return "", nil
	} else if reason := strings.TrimSpace(stderr.String()); reason != "" {
		degraded = reason
	} else {
		degraded = err.Error()
	}
	if err := m.tmux("start-server").Run(); err != nil {
		return "", fmt.Errorf("terminal: start tmux server: %w", err)
	}
	return degraded, nil
}

// KillServer tears down the whole private tmux server (and every session).
func (m *Manager) KillServer() { _ = m.tmux("kill-server").Run() }

// HasSession reports whether a tmux session named `name` already exists in this
// private server — used to re-adopt a surviving session after the cheese process
// re-execs itself (e.g. `cheese update`) without ever tearing down tmux.
func (m *Manager) HasSession(name string) bool {
	return m.tmux("has-session", "-t", name).Run() == nil
}

// Adopt wraps an already-existing tmux session (one that survived a process
// re-exec) as a Session, without spawning anything. The caller must have checked
// HasSession; the session's program keeps running untouched — only screen
// polling/relay is (re)established around it.
func (m *Manager) Adopt(name string) *Session {
	return &Session{m: m, name: name, stop: make(chan struct{})}
}

// Session is one hosted program: a tmux session polled for screen changes.
type Session struct {
	m    *Manager
	name string

	mu    sync.Mutex
	last  string
	cbs   []func()
	stop  chan struct{}
	start sync.Once
}

// Spawn launches argv in a fresh tmux session sized cols x rows, with env (a
// list of KEY=VALUE) present in the program's environment. Env is applied by
// exec'ing the program through `env`, which is both portable and inherited by
// every child process in the session.
func (m *Manager) Spawn(name string, argv, env []string, cols, rows int) (*Session, error) {
	if len(argv) == 0 {
		return nil, fmt.Errorf("terminal: empty command")
	}
	if cols <= 0 {
		cols = 200
	}
	if rows <= 0 {
		rows = 50
	}
	launch := argv
	if len(env) > 0 {
		launch = append(append([]string{"env"}, env...), argv...)
	}
	args := []string{"new-session", "-d", "-s", name,
		"-x", strconv.Itoa(cols), "-y", strconv.Itoa(rows)}
	args = append(args, launch...)
	var stderr bytes.Buffer
	cmd := m.tmux(args...)
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("terminal: spawn %q: %w: %s", name, err, stderr.String())
	}
	return &Session{m: m, name: name, stop: make(chan struct{})}, nil
}

// Snapshot returns the last polled screen contents.
func (s *Session) Snapshot() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.last
}

func (s *Session) capture() string {
	var out, stderr bytes.Buffer
	cmd := s.m.tmux("capture-pane", "-t", s.name, "-p")
	cmd.Stdout = &out
	cmd.Stderr = &stderr
	if cmd.Run() != nil {
		return ""
	}
	return out.String()
}

// Attach opens a real tmux client for this session inside a pty sized exactly to
// the viewer, and streams that client's output to onData. Because it is a true
// terminal client — not a reconstruction — the bytes carry correct wrapping,
// height, cursor position and a full repaint on connect. tmux's window follows
// the client's size (window-size latest), so the program renders at exactly the
// viewer's dimensions: what the viewer sees IS the real terminal.
func (s *Session) Attach(cols, rows int, onData func([]byte)) (*Client, error) {
	if cols <= 0 {
		cols = 80
	}
	if rows <= 0 {
		rows = 24
	}
	cmd := exec.Command(s.m.bin, "-S", s.m.sock, "-f", s.m.conf, "attach-session", "-t", s.name)
	cmd.Env = append(os.Environ(), "LC_ALL=C.UTF-8", "LANG=C.UTF-8", "TERM=xterm-256color")
	ptmx, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
	if err != nil {
		return nil, fmt.Errorf("terminal: attach %q: %w", s.name, err)
	}
	c := &Client{ptmx: ptmx, cmd: cmd}
	go func() {
		buf := make([]byte, 8192)
		for {
			n, err := ptmx.Read(buf)
			if n > 0 {
				chunk := make([]byte, n)
				copy(chunk, buf[:n])
				onData(chunk)
			}
			if err != nil {
				return
			}
		}
	}()
	return c, nil
}

// Client is one attached viewer: a tmux client process on its own pty.
type Client struct {
	ptmx *os.File
	cmd  *exec.Cmd
	once sync.Once
}

// Write sends raw viewer keystrokes to the client's pty (hence to the program).
func (c *Client) Write(p []byte) error {
	_, err := c.ptmx.Write(p)
	return err
}

// Resize changes the viewer's pty size; tmux resizes the window to match and the
// program repaints — no synthetic repaint needed.
func (c *Client) Resize(cols, rows int) error {
	if cols <= 0 || rows <= 0 {
		return nil
	}
	return pty.Setsize(c.ptmx, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
}

// Close detaches this viewer (the session itself stays alive for reattachment).
func (c *Client) Close() {
	c.once.Do(func() {
		_ = c.ptmx.Close()
		if c.cmd.Process != nil {
			_ = c.cmd.Process.Kill()
		}
		_ = c.cmd.Wait()
	})
}

// writeChunk is how much of a write goes into one `send-keys`. tmux rejects a
// command whose arguments are too long ("command too long") well before this is
// a lot of data, so a long message has to arrive in pieces. Kept comfortably
// under any tmux build's limit — the cost of a smaller value is one more
// subprocess per few kilobytes, which is nothing next to losing the message.
// (Ported from upstream micro-connector, which learned this as T-058.)
const writeChunk = 4096

// Write sends raw bytes to the session as literal input (used by the hosted
// script; viewer keystrokes go through a Client's pty instead).
func (s *Session) Write(p []byte) error {
	if len(p) == 0 {
		return nil
	}
	// A pane sitting in copy-mode (a viewer scrolled back) eats literal input;
	// leave it first so the write reaches the live program. Harmless no-op when
	// the pane is not in copy-mode.
	_ = s.m.tmux("send-keys", "-t", s.name, "-X", "cancel").Run()
	// One write becomes as many send-keys as it takes. Sending the whole thing
	// in one command is what silently dropped long messages to agents (upstream
	// T-058): tmux refused the command, the error reached only the connector's
	// log, and the agent never heard what was said to it. The pieces arrive in
	// order, so a bracketed paste stays intact — start marker in the first
	// piece, end marker in the last, one paste to the TUI.
	for len(p) > 0 {
		n := chunkEnd(p, writeChunk)
		var stderr bytes.Buffer
		cmd := s.m.tmux("send-keys", "-t", s.name, "-l", "--", string(p[:n]))
		cmd.Stderr = &stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("terminal: write %q: %w: %s", s.name, err, stderr.String())
		}
		p = p[n:]
	}
	return nil
}

// chunkEnd returns how many bytes of p to send next: at most max, and never
// splitting a UTF-8 sequence. A half-delivered rune is not cosmetic — the two
// halves are separate arguments to separate tmux commands, and neither is valid
// text — and any Chinese message long enough to be chunked would hit it.
func chunkEnd(p []byte, max int) int {
	if len(p) <= max {
		return len(p)
	}
	n := max
	// Back off to the start of the rune straddling the boundary. A UTF-8
	// continuation byte is 10xxxxxx; at most three precede their leading byte.
	for n > 0 && p[n]&0xC0 == 0x80 {
		n--
	}
	if n == 0 { // not valid UTF-8 at all: send the raw bytes rather than stall
		return max
	}
	return n
}

// OnChange registers fn and starts the poller on first use.
func (s *Session) OnChange(fn func()) {
	s.mu.Lock()
	s.cbs = append(s.cbs, fn)
	s.mu.Unlock()
	s.start.Do(func() { go s.poll() })
}

func (s *Session) poll() {
	ticker := time.NewTicker(400 * time.Millisecond)
	defer ticker.Stop()
	ticks := 0
	for {
		select {
		case <-s.stop:
			return
		case <-ticker.C:
			ticks++
			cur := s.capture()
			s.mu.Lock()
			changed := cur != s.last
			s.last = cur
			cbs := append([]func(){}, s.cbs...)
			s.mu.Unlock()
			// Fire on change, plus a periodic heartbeat (~every 1.2s) so a hosted
			// script can act on and retry against a *static* screen — e.g. an
			// unattended prompt waiting for a keypress that nothing else will emit.
			if changed || ticks%3 == 0 {
				for _, cb := range cbs {
					cb()
				}
			}
		}
	}
}

// Close kills the session and stops its poller.
func (s *Session) Close() error {
	select {
	case <-s.stop:
	default:
		close(s.stop)
	}
	return s.m.tmux("kill-session", "-t", s.name).Run()
}
