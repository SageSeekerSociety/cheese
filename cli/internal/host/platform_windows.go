package host

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/procscreen"
)

// CanHostScreens: a Windows machine keeps its screens as background processes
// and needs nothing installed for it.
func CanHostScreens() error { return nil }

// newTerminalManager: a Windows machine has no tmux. Its screens — a member's
// own Claude Code, which runs where its owner logged it in — are background
// processes procscreen keeps under the connector's config directory.
func newTerminalManager() (screens, error) {
	dir, err := screensDir()
	if err != nil {
		return nil, err
	}
	m, err := procscreen.New(dir)
	if err != nil {
		return nil, err
	}
	return procScreens{m}, nil
}

func screensDir() (string, error) {
	base, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "cheese", "screens"), nil
}

// procScreens is procscreen, seen as screens.
type procScreens struct{ m *procscreen.Manager }

func (p procScreens) HasSession(name string) bool { return p.m.HasSession(name) }
func (p procScreens) Adopt(name string) screen    { return procScreen{p.m.Adopt(name)} }
func (p procScreens) SaveIdentity(name, owner, data string) error {
	return p.m.SaveIdentity(name, owner, data)
}
func (p procScreens) Identities(owner string) (map[string]string, error) {
	return p.m.Identities(owner)
}
func (p procScreens) Spawn(name string, argv, env []string, cols, rows int) (screen, error) {
	s, err := p.m.Spawn(name, argv, env, cols, rows)
	if err != nil {
		return nil, err
	}
	return procScreen{s}, nil
}

type procScreen struct{ s *procscreen.Session }

func (p procScreen) Attach(cols, rows int, onData func([]byte)) (viewer, error) {
	v, err := p.s.Attach(cols, rows, onData)
	if err != nil {
		return nil, err
	}
	return v, nil
}
func (p procScreen) Close() error { return p.s.Close() }

// EndScreens ends every screen this machine hosts, for disconnecting it.
func EndScreens() {
	if dir, err := screensDir(); err == nil {
		if m, err := procscreen.New(dir); err == nil {
			m.EndAll()
		}
	}
}

// updateSignals: there is no signal to ask for an in-place update on Windows;
// the server's update message still reaches performUpdate over the link.
func updateSignals() []os.Signal { return nil }

// handOff starts the freshly installed binary with this process's arguments
// and exits. Windows cannot replace a running image; the screens are processes
// of their own, which the new connector adopts.
func handOff(self string) error {
	cmd := exec.Command(self, os.Args[1:]...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 | 0x00000200} // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
	if err := cmd.Start(); err != nil {
		return err
	}
	os.Exit(0)
	return nil
}
