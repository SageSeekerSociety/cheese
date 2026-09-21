package localfs

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
)

// The grant set is kept on disk, not only in memory, for one reason: the owner's
// computer is not always online and the platform is not always reachable. A device
// that forgot what it had been granted every time it restarted — or every time the
// platform was down — would answer 「没有授权」 to a folder the owner had authorized,
// which is a refusal that is both wrong and unactionable. Holding the set here is
// also what makes 「本机离线时项目照常可用」 true for this feature: the check still
// answers, from the last set it was given.
//
// It is a cache of a decision the platform made, never a source of new ones: a set
// loaded from here is re-normalized before it is used (see NewGrantSet), and a
// fingerprint the platform does not recognize is replaced on the next push.
const storeFileName = "localfs-grants.json"

// StorePath is where this machine's grant set lives, beside the config it belongs
// to. Empty cfgPath disables storage, matching how the screen-count state file
// behaves.
func StorePath(cfgPath string) string {
	if cfgPath == "" {
		return ""
	}
	return filepath.Join(filepath.Dir(cfgPath), storeFileName)
}

// LoadStore reads the last grant set this machine was given.
//
// A missing file is not an error and not an empty grant set: it means this device
// has never been told about any grant, and the caller needs to tell those two
// apart — one is 「还没收到授权」 and the other is 「授权里没有这个目录」. So a missing
// file returns (nil, nil) and the decision reports no_grant_set.
//
// A corrupt file is an error rather than a silent nil: an unreadable store means
// this machine cannot say what it is allowed to touch, and quietly behaving as if
// it had never been granted anything would hide that.
func LoadStore(path string) (*GrantSet, error) {
	if path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	var raw struct {
		DeviceID string  `json:"device_id"`
		Grants   []Grant `json:"grants"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	// Re-normalized on the way in: what is on disk is a cache, and a cache that
	// could hand back a grant that never went through Normalize would be a way to
	// put one there.
	return NewGrantSet(raw.DeviceID, raw.Grants)
}

// Save writes the grant set for the next restart to read.
//
// Written to a temporary and renamed, so a set that is half-written when the
// machine loses power is not the set the next run enforces. 0600, because the file
// names the folders on this disk that the assistant may open.
func (s *GrantSet) Save(path string) error {
	if path == "" || s == nil {
		return nil
	}
	data, err := json.MarshalIndent(struct {
		DeviceID    string  `json:"device_id"`
		Fingerprint string  `json:"fingerprint"`
		Grants      []Grant `json:"grants"`
	}{s.DeviceID, s.Fingerprint, s.Grants}, "", "  ")
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".localfs-grants-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}

// Clear removes the stored set. Used when this machine is disconnected from its
// owner: a device that has been unlinked must not keep an access key to folders it
// can no longer be told about.
func Clear(path string) error {
	if path == "" {
		return nil
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}
