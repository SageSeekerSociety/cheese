// Package runtimepath puts a directory of the connector's own first on its
// PATH, where the programs it placed or found must win over the system's: the
// Python it fetched (`devenv`), the tmux the desktop app ships (`terminal`).
//
// PATH alone does not carry that through to a session. A session's launcher
// runs in a login shell, and on macOS the login profile's path_helper moves
// /usr/bin and the other system directories back in front, so /usr/bin/python3
// (3.9) wins again. The same directories are therefore also kept in
// CHEESE_RUNTIME_PATH, which the screen is started with and the launcher puts
// first once more (`machine_launcher`).
package runtimepath

import (
	"os"
	"path/filepath"
	"strings"
)

// Var names the directories, in order, for the launcher.
const Var = "CHEESE_RUNTIME_PATH"

// Put makes dir the first entry of both PATH and Var.
func Put(dir string) {
	for _, name := range []string{"PATH", Var} {
		os.Setenv(name, first(dir, os.Getenv(name)))
	}
}

func first(dir, list string) string {
	entries := []string{dir}
	for _, entry := range filepath.SplitList(list) {
		if entry != "" && entry != dir {
			entries = append(entries, entry)
		}
	}
	return strings.Join(entries, string(os.PathListSeparator))
}
