//go:build !windows

package host

import (
	"errors"
	"os"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

var errNoScreens = errors.New("this machine hosts no screens")

// newTerminalManager is the private tmux every screen lives in. The connector
// does not start without it here: screens are part of what it offers.
func newTerminalManager() (*terminal.Manager, error) { return terminal.NewManager() }

// updateSignals are what ask the running service to update itself in place
// (`cheese update` sends SIGUSR2).
func updateSignals() []os.Signal { return []os.Signal{syscall.SIGUSR2} }

// handOff replaces this process with the freshly installed binary, keeping its
// pid, so the service manager sees no restart and the private tmux lives on.
func handOff(self string) error { return syscall.Exec(self, os.Args, os.Environ()) }
