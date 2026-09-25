package host

import (
	"errors"
	"os"
	"os/exec"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

var errNoScreens = errors.New("this machine hosts no screens: Windows devices run commands and the executor, not terminals")

// newTerminalManager: a Windows device hosts no screens. Screens live on the
// central session host; what a device runs — one-shot commands and the
// executor — needs no terminal, so there is no tmux to require.
func newTerminalManager() (*terminal.Manager, error) { return nil, nil }

// updateSignals: there is no signal to ask for an in-place update on Windows;
// the server's update message still reaches performUpdate over the link.
func updateSignals() []os.Signal { return nil }

// handOff starts the freshly installed binary with this process's arguments
// and exits. Windows cannot replace a running image, and with no screens to
// keep there is nothing a new process loses.
func handOff(self string) error {
	cmd := exec.Command(self, os.Args[1:]...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 | 0x00000200} // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
	if err := cmd.Start(); err != nil {
		return err
	}
	os.Exit(0)
	return nil
}
