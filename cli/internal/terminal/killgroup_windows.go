//go:build windows

package terminal

// killPaneGroup has nothing to signal here: Windows has no process group to
// address this way, and a pane there is stopped by killing its process.
func killPaneGroup(pid int) {
	_ = pid
}
