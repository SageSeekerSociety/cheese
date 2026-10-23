//go:build !windows

package sysproxy

// Linux and macOS have no such setting for a per-user program to read: what
// the environment says is the whole story there, exactly as before.
func readSystemProxy() (servers map[string]string, override string, enabled bool) {
	return nil, "", false
}
