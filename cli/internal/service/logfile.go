package service

import (
	"os"
	"path/filepath"
)

// logLimit is the size at which the connector log is moved aside on start.
const logLimit = 10 << 20

// logPath is the connector log beside the config.
func logPath(cfgPath string) string {
	return filepath.Join(filepath.Dir(cfgPath), "cheese.log")
}

// openLog opens the connector log for appending, first moving a log over
// logLimit to cheese.log.1 so it cannot grow without end.
func openLog(cfgPath string) (*os.File, error) {
	path := logPath(cfgPath)
	if info, err := os.Stat(path); err == nil && info.Size() > logLimit {
		_ = os.Rename(path, path+".1")
	}
	return os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
}
