//go:build !windows

package update

import "os"

// replace renames over the running executable, which Linux and macOS allow:
// the old inode stays mapped until the process exits.
func replace(tmpPath, selfPath string) error { return os.Rename(tmpPath, selfPath) }

// CleanupReplaced removes what an earlier replace left behind. Nothing here.
func CleanupReplaced(string) {}
