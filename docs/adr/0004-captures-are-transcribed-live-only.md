---
status: accepted
supersedes: ADR-0002, ADR-0003
---

# Captures are transcribed live only: no Recording, no second pass

The first version streamed audio to Deepgram for Live Captions and, on Stop, sent the whole Recording through the pre-recorded endpoint again to produce the Transcript (ADR-0003), deleting the Recording afterwards (ADR-0002). In use, Stop then took as long as uploading and transcribing the entire call, which made the app feel stuck. The user chose speed: the Transcript of a Capture is now built from the Live Captions themselves the moment Stop is pressed, and no Recording is written at all.

## Consequences

- Stop completes in the second or two it takes Deepgram to flush its last results after the stream is closed.
- The Transcript is exactly as good as the live stream. If the live connection drops, the words spoken during the reconnect are lost for good; the app reconnects with backoff and says so, but cannot recover them.
- A Capture can never be re-transcribed, and there is no Retry for Captures. Only a File can be re-transcribed.
- Deepgram timestamps restart with every live connection, so the core offsets captions by the time the connection was opened to keep paragraph timestamps continuous across reconnects.
- Audio is now sent to Deepgram once per Capture, halving the cost of the earlier design.
