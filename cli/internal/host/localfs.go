package host

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
	"github.com/SageSeekerSociety/cheese/cli/internal/localfs"
)

// 本机目录授权 on the device side: the grant set this machine holds, and the
// operations the platform may perform inside it.
//
// Two messages, both carrying their payload in Value — the same way session.list
// carries its screens — so the frozen wire protocol gains no fields:
//
//	localfs.grants  server -> device   Value: the live grant set for this machine
//	                device -> server   Value: {fingerprint, applied}   (localfs.grants.result)
//	localfs.op      server -> device   Value: {kind, path, project_id, data, …}
//	                device -> server   Value: the Reply            (localfs.op.result)
//
// The set is written to disk on every push, so a restart — or an outage of the
// platform — leaves this machine still knowing what it was granted. See
// localfs.LoadStore for why that is the point rather than an optimisation.
//
// Nothing here decides anything. Every op goes through localfs.Execute, which
// asks localfs.Decide first and only then touches the disk. The device is the
// second check, not a bypass of the first.

// loadLocalFS restores the grant set this machine was last given. Called once at
// startup, before the connection is up, so that an op arriving immediately after
// a reconnect is answered from a set rather than from nothing.
func (h *Host) loadLocalFS() {
	path := localfs.StorePath(h.cfgPath)
	set, err := localfs.LoadStore(path)
	if err != nil {
		// Not fatal: the machine simply has no set until the platform pushes one.
		// Saying so is better than starting with an empty set and no explanation.
		fmt.Fprintf(os.Stderr, "cheese: 本机目录授权: 读取本机的授权表失败（%v）— 在平台重新下发前，本机不会放行任何目录\n", err)
		return
	}
	h.localFSMu.Lock()
	h.localFSSet = set
	h.localFSMu.Unlock()
	if set != nil {
		fmt.Fprintf(os.Stderr, "cheese: 本机目录授权: 已载入 %d 条授权（%s）\n", len(set.Grants), set.Fingerprint)
	}
}

// localFS returns the current set under the lock.
func (h *Host) localFS() *localfs.GrantSet {
	h.localFSMu.Lock()
	defer h.localFSMu.Unlock()
	return h.localFSSet
}

// setLocalFSGrants replaces the set with what the platform just sent, persists it,
// and answers with the fingerprint this machine is now holding.
//
// Replacing wholesale rather than merging is deliberate: a grant missing from the
// pushed set is a revoked grant, and merging would make a revoke impossible to
// express — the device would keep honoring a grant its owner had just taken away.
func (h *Host) setLocalFSGrants(m link.Msg) {
	reply := link.Msg{T: "localfs.grants.result", ID: m.ID}

	var payload struct {
		DeviceID    string          `json:"device_id"`
		Fingerprint string          `json:"fingerprint"`
		Grants      []localfs.Grant `json:"grants"`
	}
	if err := decodeValue(m.Value, &payload); err != nil {
		reply.Error = "授权表无法解析: " + err.Error()
		_ = h.conn.Send(reply)
		return
	}

	set, err := localfs.NewGrantSet(payload.DeviceID, payload.Grants)
	if err != nil {
		reply.Error = err.Error()
		_ = h.conn.Send(reply)
		return
	}
	h.localFSMu.Lock()
	h.localFSSet = set
	h.localFSMu.Unlock()

	// Persisted after the in-memory swap, so an op racing this push is answered
	// from the new set either way; a save that fails costs the next restart, not
	// this run, and it is reported rather than swallowed.
	if err := set.Save(localfs.StorePath(h.cfgPath)); err != nil {
		fmt.Fprintf(os.Stderr, "cheese: 本机目录授权: 授权表落盘失败（%v）— 本次运行有效，重启后会退回上一份\n", err)
	}

	reply.Value = map[string]any{
		"fingerprint": set.Fingerprint,
		"applied":     true,
	}
	_ = h.conn.Send(reply)
}

// runLocalFSOp answers one operation, in a goroutine: a read of a large file or a
// listing of a real directory must not block the control channel.
func (h *Host) runLocalFSOp(m link.Msg) {
	var op localfs.Op
	if err := decodeValue(m.Value, &op); err != nil {
		_ = h.conn.Send(link.Msg{T: "localfs.op.result", ID: m.ID,
			Error: "无法解析请求: " + err.Error()})
		return
	}
	reply := localfs.Execute(h.localFS(), op)
	_ = h.conn.Send(link.Msg{T: "localfs.op.result", ID: m.ID, Value: reply})
}

// decodeValue re-decodes the untyped Value the wire carries into a typed payload.
// Msg.Value is `any` because the protocol is a flat union — the server's shape is
// unknown to this package — so the round trip through JSON is what gives the
// handler a typed request instead of a map it would index by hand.
func decodeValue(value any, into any) error {
	if value == nil {
		return fmt.Errorf("请求没有内容")
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, into)
}
