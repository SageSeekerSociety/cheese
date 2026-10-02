// Wait until Date.now() has moved past the current millisecond.
//
// Vue drops an event whose first Vue handler ran in the same millisecond that
// the target listener was attached (runtime-dom stamps `event._vts = Date.now()`
// and skips any invoker with `_vts <= invoker.attached`). That guards against
// the click that mounts a listener also firing it. A test that clicks a button
// which mounts an element and then dispatches a pointer event on that element
// can land inside the same millisecond, and the event is silently ignored
// whenever an ancestor carries its own Vue listener for that event type. A real
// pointer never arrives that fast, so call this between the two steps.
export async function nextMillisecond(): Promise<void> {
  const now = Date.now()
  while (Date.now() <= now) await new Promise((resolve) => setTimeout(resolve, 1))
}
