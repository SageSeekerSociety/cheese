package update

import "os"

// replace moves the running executable aside and the new one into its place.
var replace = replaceAside

// removeReplaced removes the binary an update moved aside, once the process
// that was running it has gone.
func removeReplaced(selfPath string) { _ = os.Remove(selfPath + ".old") }
