// Package procscreen keeps a machine's hosted programs as plain background
// processes, where there is no tmux to keep them in: Windows.
//
// It answers the same questions the tmux manager does, so the host treats a
// screen here as it treats one there. What tmux keeps in the session itself —
// that it exists, which process runs it, who launched it and with what — is
// kept here in one small file per screen. The process does not belong to the
// connector: it outlives a restart or an update of it, and the next connector
// finds it by its file and adopts it, as one re-adopts a tmux session.
//
// A screen whose program has exited stays until it is closed, as a tmux pane
// does (`remain-on-exit`), so a reconnect adopts it rather than starting the
// program again. A reboot ends every screen, so a file left from before the
// machine last booted is dropped.
//
// There is no terminal to watch or type into: a viewer sees nothing here.
package procscreen

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ErrNoView is what attaching a viewer answers: the program has no terminal.
var ErrNoView = errors.New("this machine runs its screens without a terminal to show")

// record is what one screen's file holds.
type record struct {
	PID int `json:"pid"`
	// When that process started, in the units system.started reports, so a pid
	// reused by an unrelated process is not taken for it.
	Started  int64  `json:"started"`
	Owner    string `json:"owner,omitempty"`
	Identity string `json:"identity,omitempty"`
}

// system is what the operating system does for a screen.
type system interface {
	// start launches argv with env added, its output appended to log, and
	// reports its pid and when it started.
	start(argv, env []string, log string) (pid int, started int64, err error)
	// running reports whether the process with that pid and start is still there.
	running(pid int, started int64) bool
	// end ends the process and everything it started.
	end(pid int) error
	// booted is when this boot began, in the same units as start.
	booted() int64
}

// Manager keeps the screens of one machine under dir.
type Manager struct {
	dir string
	sys system
}

func newManager(dir string, sys system) (*Manager, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("procscreen: %w", err)
	}
	return &Manager{dir: dir, sys: sys}, nil
}

func (m *Manager) file(name string) string { return filepath.Join(m.dir, name+".json") }

// Log is where a screen's program writes what it prints.
func (m *Manager) Log(name string) string { return filepath.Join(m.dir, name+".log") }

func (m *Manager) read(name string) (record, bool) {
	data, err := os.ReadFile(m.file(name))
	if err != nil {
		return record{}, false
	}
	var rec record
	if json.Unmarshal(data, &rec) != nil {
		return record{}, false
	}
	return rec, true
}

func (m *Manager) write(name string, rec record) error {
	data, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	tmp := m.file(name) + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, m.file(name))
}

func (m *Manager) forget(name string) {
	_ = os.Remove(m.file(name))
	_ = os.Remove(m.Log(name))
}

// stale reports a screen from before this boot: its program is gone with the
// boot it ran in, whatever its file says.
func (m *Manager) stale(rec record) bool {
	return !m.sys.running(rec.PID, rec.Started) && rec.Started < m.sys.booted()
}

// HasSession reports whether the screen exists: its program running, or exited
// this boot and not yet closed.
func (m *Manager) HasSession(name string) bool {
	rec, ok := m.read(name)
	if !ok {
		return false
	}
	if m.stale(rec) {
		m.forget(name)
		return false
	}
	return true
}

// Spawn starts argv as the screen name, with env (KEY=VALUE) added to what it
// inherits. The size is a terminal's, and there is none.
func (m *Manager) Spawn(name string, argv, env []string, _, _ int) (*Session, error) {
	if len(argv) == 0 {
		return nil, fmt.Errorf("procscreen: empty command")
	}
	pid, started, err := m.sys.start(argv, env, m.Log(name))
	if err != nil {
		return nil, fmt.Errorf("procscreen: spawn %q: %w", name, err)
	}
	if err := m.write(name, record{PID: pid, Started: started}); err != nil {
		_ = m.sys.end(pid)
		return nil, fmt.Errorf("procscreen: keep %q: %w", name, err)
	}
	return &Session{m: m, name: name}, nil
}

// Adopt is a screen that already exists; the caller has checked HasSession.
func (m *Manager) Adopt(name string) *Session { return &Session{m: m, name: name} }

// SaveIdentity keeps who launched the screen and how, for the next connector.
func (m *Manager) SaveIdentity(name, owner, data string) error {
	rec, ok := m.read(name)
	if !ok {
		return fmt.Errorf("procscreen: no screen %q", name)
	}
	rec.Owner, rec.Identity = owner, data
	return m.write(name, rec)
}

// Identities is every screen owner launched, by name, with what it saved.
func (m *Manager) Identities(owner string) (map[string]string, error) {
	entries, err := os.ReadDir(m.dir)
	if err != nil {
		return nil, fmt.Errorf("procscreen: %w", err)
	}
	identities := map[string]string{}
	for _, entry := range entries {
		name, isRecord := strings.CutSuffix(entry.Name(), ".json")
		if !isRecord || entry.IsDir() {
			continue
		}
		rec, ok := m.read(name)
		if !ok {
			continue
		}
		if m.stale(rec) {
			m.forget(name)
			continue
		}
		if rec.Owner == owner && rec.Identity != "" {
			identities[name] = rec.Identity
		}
	}
	return identities, nil
}

// EndAll ends every screen on this machine, for disconnecting it.
func (m *Manager) EndAll() {
	entries, _ := os.ReadDir(m.dir)
	for _, entry := range entries {
		if name, isRecord := strings.CutSuffix(entry.Name(), ".json"); isRecord {
			_ = (&Session{m: m, name: name}).Close()
		}
	}
}

// Session is one hosted program.
type Session struct {
	m    *Manager
	name string
}

// Attach has nothing to show: the program runs without a terminal.
func (s *Session) Attach(int, int, func([]byte)) (Viewer, error) { return nil, ErrNoView }

// Close ends the program and everything it started, and forgets the screen.
func (s *Session) Close() error {
	rec, ok := s.m.read(s.name)
	if ok && s.m.sys.running(rec.PID, rec.Started) {
		if err := s.m.sys.end(rec.PID); err != nil {
			return fmt.Errorf("procscreen: end %q: %w", s.name, err)
		}
	}
	s.m.forget(s.name)
	return nil
}

// Viewer is a terminal a viewer types into; a screen here never has one.
type Viewer interface {
	Write([]byte) error
	Resize(cols, rows int) error
	Close()
}
