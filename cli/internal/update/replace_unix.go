//go:build !windows

package update

import "os"

// replace renames over the running executable, which Linux and macOS allow:
// the old inode stays mapped until the process exits. A variable so that a
// test can run Windows' replaceAside here.
var replace = os.Rename

// removeReplaced removes what an earlier replace moved aside. Nothing here.
func removeReplaced(string) {}
