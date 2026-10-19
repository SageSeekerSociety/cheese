//go:build !windows

package awake

import (
	"errors"
	"os"
	"os/exec"
	"runtime"
	"strconv"
)

func AcquireSystem() (func(), error) {
	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		// -i holds PreventUserIdleSystemSleep; -w prevents an orphan assertion
		// after a connector crash. The host releases it before an exec update.
		cmd = exec.Command("/usr/bin/caffeinate", "-i", "-w", strconv.Itoa(os.Getpid()))
	} else {
		path, err := exec.LookPath("systemd-inhibit")
		if errors.Is(err, exec.ErrNotFound) {
			return func() {}, nil
		}
		if err != nil {
			return nil, err
		}
		cmd = exec.Command(path, "--what=idle:sleep", "--mode=block", "--who=Cheese", "--why=AI sessions are running", "cat")
	}
	input, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		input.Close()
		return nil, err
	}
	return func() { input.Close(); _ = cmd.Process.Kill(); _ = cmd.Wait() }, nil
}
