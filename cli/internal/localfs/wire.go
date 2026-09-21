package localfs

import (
	"errors"
	"fmt"
)

// OpKind is one thing the platform can ask this machine to do in a granted
// directory. It is a closed set on purpose: an unrecognised kind is refused rather
// than ignored, because the failure mode of a silently-ignored write is a caller
// that believes it saved.
type OpKind string

const (
	OpRead  OpKind = "read"
	OpWrite OpKind = "write"
	OpList  OpKind = "list"
)

// Op is one request against the granted directories.
type Op struct {
	Kind      OpKind `json:"kind"`
	Path      string `json:"path"`
	ProjectID string `json:"project_id,omitempty"`
	// Data is the content of a write. It travels base64-encoded on the wire; the
	// encoding is the transport's business, not this type's.
	Data []byte `json:"data,omitempty"`
	// Depth and Exclude bound a listing. See ListOptions for why both exist.
	Depth   int      `json:"depth,omitempty"`
	Exclude []string `json:"exclude,omitempty"`
}

// Reply is the answer to one Op.
//
// Decision and Reason describe the *authorization*, and they are the same strings
// the platform uses, because they end up in one audit trail. Error is separate and
// exists because a failure after an allow is not a denial: a file that was
// authorized and then could not be read was authorized, and writing 「拒绝」 into
// the audit for it would be a lie about what happened.
type Reply struct {
	Decision Decision `json:"decision"`
	Reason   string   `json:"reason"`
	Detail   string   `json:"detail"`
	// Path is the canonical path the decision was made about — expanded, dot-free,
	// with the owner's own case preserved. It is what a refusal names.
	Path string `json:"path,omitempty"`
	// Data is the content of a read. Base64 on the wire.
	Data []byte `json:"data,omitempty"`
	// Entries is the result of a listing.
	Entries []Entry `json:"entries,omitempty"`
	// Truncated says the listing hit its cap and is not the whole directory.
	Truncated bool `json:"truncated,omitempty"`
	// Error is a failure that happened after the access was authorized. Empty for a
	// denial, whose explanation is Detail.
	Error string `json:"error,omitempty"`
}

// Allowed reports whether the access was authorized. A reply that was allowed can
// still carry Error — see the type comment for why those two are not the same
// question.
func (r Reply) Allowed() bool { return r.Decision == DecisionAllowed }

// Execute answers one Op against this machine's grant set: decide first, and touch
// the disk only if the decision allowed it.
//
// The ordering is the feature. There is no branch here that opens a path before
// Decide has seen it, and the path it opens is the one Decide resolved — not a
// second resolution, which could land somewhere the verdict never looked at.
func Execute(set *GrantSet, op Op) Reply {
	needed := ModeRead
	if op.Kind == OpWrite {
		needed = ModeReadWrite
	}

	verdict := Decide(set, Request{
		Path:      op.Path,
		Needed:    needed,
		ProjectID: op.ProjectID,
	})
	reply := Reply{
		Decision: verdict.Decision,
		Reason:   verdict.Reason,
		Detail:   verdict.Detail,
		Path:     verdict.Resolved.Lexical.Text,
	}
	if !verdict.Allowed() {
		return reply
	}

	var (
		data      []byte
		entries   []Entry
		truncated bool
		err       error
	)
	switch op.Kind {
	case OpRead:
		data, err = ReadFile(verdict.Resolved)
		reply.Data = data
	case OpWrite:
		err = WriteFile(verdict.Resolved, op.Data, 0o644)
	case OpList:
		entries, err = ListDir(verdict.Resolved, ListOptions{
			Depth:   op.Depth,
			Exclude: op.Exclude,
		})
		reply.Entries = entries
		var tooLarge *ErrTooLarge
		if errors.As(err, &tooLarge) {
			// A listing that hit its cap is still a useful listing; it is reported
			// as incomplete rather than as a failure, because the caller can act on
			// "there is more, narrow it".
			truncated = true
			err = nil
		}
	default:
		// Not an error path that can be reached by a caller who read this file, and
		// deliberately not a silent success: an unknown kind means the two ends
		// disagree about the protocol.
		err = fmt.Errorf("unknown localfs op %q", op.Kind)
	}
	reply.Truncated = truncated
	if err != nil {
		reply.Error = err.Error()
	}
	return reply
}
