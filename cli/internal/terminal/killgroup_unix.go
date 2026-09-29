//go:build !windows

package terminal

import "syscall"

// killPaneGroup stops everything a pane started.
//
// tmux gives each pane a session of its own, so the pane's pid is also its
// process group id and a negative pid reaches the whole group — which is what
// an agent's launcher needs: the runner under the shell, and whatever the
// runner started, are not this process's children to walk.
func killPaneGroup(pid int) {
	_ = syscall.Kill(-pid, syscall.SIGKILL)
}
