//go:build !windows

package host

import (
	"os"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

// CanHostScreens reports whether the service will find the tmux it does not
// start without.
func CanHostScreens() error { return terminal.Present() }

// newTerminalManager is the private tmux every screen lives in. The connector
// does not start without it here: screens are part of what it offers.
func newTerminalManager() (screens, error) {
	m, err := terminal.NewManager()
	if err != nil {
		return nil, err
	}
	return tmuxScreens{m}, nil
}

// tmuxScreens is the private tmux server, seen as screens.
type tmuxScreens struct{ m *terminal.Manager }

func (t tmuxScreens) HasSession(name string) bool { return t.m.HasSession(name) }
func (t tmuxScreens) Adopt(name string) screen    { return tmuxScreen{t.m.Adopt(name)} }
func (t tmuxScreens) SaveIdentity(name, owner, data string) error {
	return t.m.SaveIdentity(name, owner, data)
}
func (t tmuxScreens) Identities(owner string) (map[string]string, error) {
	return t.m.Identities(owner)
}
func (t tmuxScreens) Spawn(name string, argv, env []string, cols, rows int) (screen, error) {
	s, err := t.m.Spawn(name, argv, env, cols, rows)
	if err != nil {
		return nil, err
	}
	return tmuxScreen{s}, nil
}

type tmuxScreen struct{ s *terminal.Session }

func (t tmuxScreen) Attach(cols, rows int, onData func([]byte)) (viewer, error) {
	c, err := t.s.Attach(cols, rows, onData)
	if err != nil {
		return nil, err
	}
	return c, nil
}
func (t tmuxScreen) Close() error { return t.s.Close() }

// EndScreens ends every screen this machine hosts, for disconnecting it.
func EndScreens() {
	if m, err := terminal.NewManager(); err == nil {
		m.KillServer()
	}
}

// updateSignals are what ask the running service to update itself in place
// (`cheese update` sends SIGUSR2).
func updateSignals() []os.Signal { return []os.Signal{syscall.SIGUSR2} }

// handOff replaces this process with the freshly installed binary, keeping its
// pid, so the service manager sees no restart and the private tmux lives on.
func handOff(self string) error { return syscall.Exec(self, os.Args, os.Environ()) }
