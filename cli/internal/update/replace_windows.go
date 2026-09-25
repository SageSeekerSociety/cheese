package update

import "os"

// replace swaps the new binary in. Windows refuses to overwrite or delete a
// running executable but does let it be renamed, so the running one moves
// aside first and is removed by the next process (CleanupReplaced).
func replace(tmpPath, selfPath string) error {
	old := selfPath + ".old"
	_ = os.Remove(old)
	if err := os.Rename(selfPath, old); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, selfPath); err != nil {
		_ = os.Rename(old, selfPath)
		return err
	}
	return nil
}

// CleanupReplaced removes the binary an update moved aside, once the process
// that was running it has gone.
func CleanupReplaced(selfPath string) { _ = os.Remove(selfPath + ".old") }
