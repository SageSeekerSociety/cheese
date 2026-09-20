# demo-capture

Record the app on screen: for a demo shown to someone, or to show what a bug
looks like. Frames come from the stream `agent-browser` already serves, so this
records whatever browser the `browser` skill is driving and never sends input of
its own.

Takes are written to `~/cheese-recordings` by default, never into the repo: one
ten-minute take is a few thousand JPEG frames.

## Prerequisites

- A browser lane already running under web-plane (the `browser` skill sets this up).
- `node` and `ffmpeg` on PATH.

## Recording

```bash
# 1. Turn on the stream and read the port it picked
agent-browser stream enable            # prints the port; `stream status` shows it again

# 2. Start capturing (prints the capture PID)
scripts/dev/demo-capture/start-capture.sh <port> student-flow

# 3. Drive the app as the demo should look

# 4. Stop, which writes the take's stop record
scripts/dev/demo-capture/stop-capture.sh student-flow

# 5. Encode, keeping the real timing of every frame
scripts/dev/demo-capture/encode-stream.py \
  ~/cheese-recordings/student-flow demo.mp4

# 6. Release the stream
agent-browser stream disable
```

`encode-stream.py` takes an optional third argument, a number of seconds: frames
before that mark are dropped, which is how you cut a slow login or first page
load off the front.

## How it works

`capture-stream.mjs` saves each frame as a JPEG plus a line in `frames.jsonl`
recording when that frame arrived. `encode-stream.py` then builds an ffmpeg
concat list from those timestamps, so the video plays back at the speed the app
actually ran. Encoding at a fixed frame rate instead would stretch the pauses
and compress the fast parts.

A capture stops itself after 20 minutes. A forgotten one would otherwise keep
filling the disk with frames.

## Known gap

The captured frames do not show where the mouse is: the stream delivers the
page, and the pointer is drawn by the OS, not the page. Any demo that needs a
visible pointer has to draw one, either into the page during the take or into
the frames while encoding. Nothing here does that yet.
