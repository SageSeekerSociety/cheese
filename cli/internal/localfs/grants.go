package localfs

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
)

// Mode is how much of the directory a grant exposes.
//
// Two modes, not a permission matrix: read-only is for material the assistant
// should read and cannot damage (a grade sheet, a roster, a set of reference
// files), and read-write is for a working folder it is meant to change. A read
// grant never satisfies a write, which is the whole reason the distinction is
// carried across the wire rather than inferred on this side.
type Mode string

const (
	ModeRead      Mode = "read"
	ModeReadWrite Mode = "read_write"
)

// Permits reports whether a grant held at this mode is enough for a request
// needing `needed`. Read-write covers read; read does not cover read-write.
func (m Mode) Permits(needed Mode) bool {
	if m == ModeReadWrite {
		return true
	}
	return needed == ModeRead
}

// Scope is how far a grant reaches across the owner's own work.
//
// Project scope is this one piece of work, so a folder authorized for a term
// paper is not silently usable by next month's task. User scope is every one of
// the owner's matters, until they revoke it.
type Scope string

const (
	ScopeProject Scope = "project"
	ScopeUser    Scope = "user"
)

// Grant is one authorized directory on this machine.
//
// Path is the canonical text the owner was shown; Segments is the normalized form
// the decision is made on. Both are carried, because a grant that can only be
// compared cannot be shown and one that can only be shown cannot be compared
// safely.
type Grant struct {
	ID        string   `json:"id"`
	Path      string   `json:"path"`
	Platform  Platform `json:"platform"`
	Mode      Mode     `json:"mode"`
	Scope     Scope    `json:"scope"`
	ProjectID string   `json:"project_id,omitempty"`

	// normalized is derived from Path+Platform and is never taken off the wire.
	normalized NormalizedPath
}

// fingerprinted is the identity of a grant for the purpose of noticing that the
// set changed. Revocation is deliberately absent: a revoked grant leaves the set,
// and the fingerprint of the set without it differs from the one with it, which is
// how a revoke reaches a device that was offline when it happened.
func (g Grant) fingerprintPart() string {
	return g.ID + "|" + g.Key() + "|" + string(g.Mode) + "|" + string(g.Scope) + "|" + g.ProjectID
}

// Key is the folded comparison key, or the raw path if the grant could not be
// normalized. A grant that will not normalize can never contain anything — the
// decision refuses it by name — so the key is only ever used for the fingerprint.
func (g Grant) Key() string {
	if g.normalized.Root == "" && len(g.normalized.Segments) == 0 {
		return g.Path
	}
	return g.normalized.Key
}

// Valid reports whether this grant survived normalization. A grant whose path
// cannot be normalized is not silently dropped (the owner would be told they
// authorized something that is not in effect); it is carried and refused at the
// point of decision, with the reason.
func (g Grant) Valid() bool {
	return g.normalized.Root != "" || len(g.normalized.Segments) > 0
}

// CoversProject reports whether this grant applies to work in projectID.
func (g Grant) CoversProject(projectID string) bool {
	if g.Scope == ScopeUser {
		return true
	}
	return projectID != "" && g.ProjectID == projectID
}

// GrantSet is the set of grants this device holds, as the platform last sent it.
//
// The device keeps its own copy on purpose: the files are here, so the check has
// to work when the platform is unreachable, rolled back, or wrong. The platform's
// answer is the first one; this is the second, and neither is trusted alone.
type GrantSet struct {
	DeviceID    string  `json:"device_id"`
	Fingerprint string  `json:"fingerprint"`
	Grants      []Grant `json:"grants"`
}

// NewGrantSet normalizes every grant and computes the set's fingerprint.
//
// A grant that will not normalize is kept rather than dropped, and it is the
// decision (not this constructor) that refuses it by name — a set that quietly
// discarded a bad grant would look identical to a set where the owner never
// authorized anything, and the assistant would be told 「没有授权」 about a folder
// the owner had just authorized. The refusal has to be visible, and it has to name
// the real reason.
func NewGrantSet(deviceID string, grants []Grant) (*GrantSet, error) {
	out := make([]Grant, 0, len(grants))
	for _, g := range grants {
		if g.Platform == "" {
			// A grant with no platform cannot be normalized at all, and guessing
			// the platform is guessing the case rules, which is guessing the
			// containment answer.
			g.Platform = PlatformLinux
		}
		normalized, err := Normalize(g.Path, g.Platform)
		if err == nil && !normalized.IsRoot() {
			g.normalized = normalized
		}
		// A grant covering a whole disk (`/`, `C:/`) is refused here as well as on
		// the platform, and refused by being left un-normalized rather than
		// dropped. Neither side alone is trusted: the platform refuses it when the
		// owner asks, and this refuses it again in case the set arrived from a
		// platform that is buggy, rolled back, or lying. A dropped grant would be
		// invisible; an invalid one is skipped by every decision and still counts
		// in the fingerprint, so the owner can see the set changed.
		out = append(out, g)
	}
	set := &GrantSet{DeviceID: deviceID, Grants: out}
	set.Fingerprint = Fingerprint(out)
	return set, nil
}

// Fingerprint is the identity of a grant set: same grants in, same string out,
// regardless of the order they were sent in.
func Fingerprint(grants []Grant) string {
	parts := make([]string, 0, len(grants))
	for _, g := range grants {
		parts = append(parts, g.fingerprintPart())
	}
	sort.Strings(parts)
	sum := sha256.Sum256([]byte(strings.Join(parts, ";")))
	return hex.EncodeToString(sum[:16])
}

// Decision is the answer to one path question.
type Decision string

const (
	DecisionAllowed Decision = "allowed"
	DecisionDenied  Decision = "denied"
)

// Verdict is the answer to whether a path may be touched, with the reason kept for
// the screen.
//
// The reason codes are the same strings the platform uses
// (backend/app/domain/local_fs/service.py), because they end up in one audit
// trail and a reader must not have to know which side answered.
type Verdict struct {
	Decision Decision
	Reason   string
	Detail   string
	Grant    *Grant
}

// Allowed reports whether the request may proceed.
func (v Verdict) Allowed() bool { return v.Decision == DecisionAllowed }

// Request is one question about one path.
type Request struct {
	// Path is the path as the caller wrote it. It is resolved on this machine
	// (see ResolvePath) before it is judged, because the platform cannot resolve a
	// symlink that lives on somebody else's disk.
	Path string
	// Needed is the access the caller wants. A read and a write of the same path
	// are two different questions with two different answers.
	Needed Mode
	// ProjectID is the work this question is for, so a project-scoped grant cannot
	// be used by another project on the same machine. Empty means the caller could
	// not say, and then only a user-scoped grant can cover it.
	ProjectID string
}

// Decide answers one path question from this device's own copy of the grant set.
//
// The order is the same one the platform uses, and it matters: the path is
// resolved and normalized first (a path that will not normalize is a denial, not
// an error), then a covering grant is looked for, and only then is the covering
// grant asked whether its mode is enough. Reporting 「这个目录只授权了读取」 rather
// than 「越界」 is what makes the refusal actionable.
func Decide(set *GrantSet, req Request) Verdict {
	if set == nil {
		// No set at all is not "allow" and it is not "deny for no reason": the
		// device has never been told about any grant, which is a state the person
		// reading the refusal has to be able to act on.
		return Verdict{
			Decision: DecisionDenied,
			Reason:   "no_grant_set",
			Detail:   "这台设备还没有收到任何授权，请先在「我的电脑」里授权目录",
		}
	}

	resolved, err := ResolvePath(req.Path)
	if err != nil {
		refusal, ok := AsRefusal(err)
		if !ok {
			return Verdict{
				Decision: DecisionDenied,
				Reason:   "unresolvable",
				Detail:   err.Error(),
			}
		}
		return Verdict{Decision: DecisionDenied, Reason: refusal.Reason, Detail: refusal.Detail}
	}

	covering := make([]Grant, 0, len(set.Grants))
	for _, g := range set.Grants {
		if !g.CoversProject(req.ProjectID) {
			continue
		}
		if !g.Valid() {
			continue
		}
		if containsResolved(g, resolved) {
			covering = append(covering, g)
		}
	}

	if len(covering) == 0 {
		return Verdict{
			Decision: DecisionDenied,
			Reason:   "no_grant",
			Detail:   "这个路径不在任何授权目录里",
		}
	}

	for i := range covering {
		if covering[i].Mode.Permits(req.Needed) {
			grant := covering[i]
			return Verdict{
				Decision: DecisionAllowed,
				Reason:   "granted",
				Detail:   "已授权",
				Grant:    &grant,
			}
		}
	}

	// Something covers the path, but not for this much. Reporting the narrower
	// cause separately is what lets the refusal be actionable.
	narrowest := covering[0]
	return Verdict{
		Decision: DecisionDenied,
		Reason:   "read_only_grant",
		Detail:   "这个目录只授权了读取，不能写入",
		Grant:    &narrowest,
	}
}

// containsResolved reports whether a path that has already been resolved to its
// real form lies inside this grant.
//
// Two checks, and both must hold:
//
//   - Lexical: the normalized path is inside the normalized grant. This is what
//     makes the owner's own text authoritative — 「我授权的是 /home/alice/MyDocs」
//     must mean that name.
//   - Real: the resolved path is inside the resolved grant. This is what stops a
//     symlink planted inside the granted directory from reaching out of it, which
//     is the escape that a purely lexical check cannot see.
//
// When the grant itself cannot be resolved — the directory was moved or deleted —
// the real check is skipped rather than failed, because the alternative is
// refusing an access the owner plainly granted. The lexical check still applies,
// and a read of a directory that no longer exists fails at the filesystem anyway.
func containsResolved(g Grant, resolved ResolvedPath) bool {
	if !g.Valid() {
		return false
	}
	if !Contains(g.normalized, resolved.Lexical) {
		return false
	}
	if !resolved.RealKnown {
		return true
	}
	grantReal, err := ResolvePath(g.Path)
	if err != nil || !grantReal.RealKnown {
		// The grant does not resolve; the lexical answer stands.
		return true
	}
	return Contains(grantReal.Real, resolved.Real)
}

// String renders a set for logs, without ever printing the paths themselves: a
// grant is an access key to somebody's disk, and a log line is the easiest place
// for one to leak.
func (s *GrantSet) String() string {
	if s == nil {
		return "localfs: no grant set"
	}
	return fmt.Sprintf("localfs: device=%s grants=%d fingerprint=%s",
		s.DeviceID, len(s.Grants), s.Fingerprint)
}
