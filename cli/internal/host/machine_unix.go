//go:build !windows

package host

import (
	"crypto/sha256"
	"fmt"
	"os"
)

// platformSocket is where the sandbox of the state at resolved reaches the
// platform: a directory of the machine's /tmp beside the executor's own
// (`runtime_core.platform_socket_directory` names the same one), shown to the
// sandbox read-only.
func platformSocket(resolved string) string {
	digest := sha256.Sum256([]byte(resolved))
	return fmt.Sprintf("/tmp/cheese-platform-%d-%x/platform.sock", os.Getuid(), digest[:12])
}
