package link

import "encoding/base64"

// The frames the connector writes up: two for one execution.call, its
// Heartbeat, and the machine.* frames of a stream its sandbox opened.
//
// They live here beside Msg, not inline at the call site in
// cli/internal/host/executor.go, because nothing at this seam imports anything
// across it: the only thing holding the Go writer and the backend reader
// together is backend/tests/fixtures/wire/*.json, and a
// fixture can only hold code that actually builds the frame. Built inline, the
// fixture check in frames_test.go would be a second hand-written copy of the
// construction, agreeing with the fixture while the shipped one drifts.

// ExecutionData carries one chunk of the executor's answer: base64 of the raw
// bytes read off the executor socket.
func ExecutionData(id string, payload []byte) Msg {
	return Msg{
		T:    "execution.data",
		ID:   id,
		Data: base64.StdEncoding.EncodeToString(payload),
	}
}

// ExecutionResult ends one call. A nil err leaves Error empty and omitempty
// drops the key, which is how the backend tells "the machine failed" from "the
// call finished".
func ExecutionResult(id string, err error) Msg {
	m := Msg{T: "execution.result", ID: id}
	if err != nil {
		m.Error = err.Error()
	}
	return m
}

// ExecOutput carries one line a running exec wrote to stderr, without its line
// end, for an exec that asked for Stream. The exec.result after it still
// carries the whole of stderr: these frames are what the backend shows while
// the command runs, never what it decides the outcome from.
func ExecOutput(id, line string) Msg {
	return Msg{T: "exec.output", ID: id, Stderr: line}
}

// Heartbeat says this connector is still there. The ping below it keeps the
// connection alive at the socket, but that is answered inside the server's
// websocket layer and never reaches the backend; this frame does, and a link
// the backend stops hearing it on is taken offline (backend
// `DeviceHub.silence_allowed`), so calls to a machine that went to sleep fail
// at once instead of waiting for the socket to close.
func Heartbeat() Msg {
	return Msg{T: "heartbeat"}
}

// MachineOpen starts one stream a session's sandbox opened on its platform
// socket. Path is the state directory as the backend named it in the
// execution.call that made the connector listen there: which socket a stream
// arrived on is the only thing that says whose it is.
func MachineOpen(id, path string) Msg {
	return Msg{T: "machine.open", ID: id, Path: path}
}

// MachineData carries bytes of one machine stream, base64 like ExecutionData,
// in both directions.
func MachineData(id string, payload []byte) Msg {
	return Msg{T: "machine.data", ID: id, Data: base64.StdEncoding.EncodeToString(payload)}
}

// MachineEnd says the sandbox's side finished writing (it half-closed or its
// read hit end of file); the answer may still be coming down.
func MachineEnd(id string) Msg {
	return Msg{T: "machine.end", ID: id}
}

// MachineClose ends one machine stream. Up, the sandbox's connection is gone
// (Error says why when it failed); down, the platform has answered in full.
func MachineClose(id string, err error) Msg {
	m := Msg{T: "machine.close", ID: id}
	if err != nil {
		m.Error = err.Error()
	}
	return m
}
