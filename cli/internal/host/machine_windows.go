//go:build windows

package host

// Windows has no sandbox (an isolated room is refused there), so no session
// on it has a platform socket to serve.
func platformSocket(string) string { return "" }
