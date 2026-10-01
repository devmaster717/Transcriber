# Transcriber

A personal desktop app that turns audio into text. Audio comes from a file on disk, the microphone, or the computer's own sound output (a Zoom or Teams call), and the text comes back from Deepgram.

## Language

**Transcript**:
The punctuated, paragraphed text of one piece of audio, with each paragraph attributed to a Speaker and given a start time. A Transcript keeps all of that even when a Rendering leaves some of it out.
_Avoid_: transcription (use only for the act of producing a Transcript), text, output

**Rendering**:
A plain-text view of a Transcript, with timestamps and speaker labels each optionally included. The file the user reads is a Rendering; the Transcript behind it is not changed by choosing a different Rendering.
_Avoid_: export, format, view

**Speaker**:
One distinct voice in a Transcript, as separated by Deepgram. A Speaker is a label, not a known person.
_Avoid_: participant, user, voice

**Source**:
Where the audio for a Transcript comes from. There are three kinds: a File (an audio file already on disk), the Microphone, and System Audio (whatever the computer is playing, such as a call).
_Avoid_: input, upload (nothing is uploaded to a server; a File is opened locally), device (a device is how a Source is captured, not the Source itself)

**Capture**:
One live listening session, from Start to Stop, taking audio from the Microphone, System Audio, or both at once (a Meeting Capture).
_Avoid_: session, recording session, stream

**Meeting Capture**:
A Capture that takes the Microphone and System Audio together, so the Transcript covers both sides of a call. The Microphone side is attributed to the Speaker "You".
_Avoid_: call capture, dual capture

**Live Captions**:
The text shown on screen while a Capture is running, built line by line as Deepgram returns it. When the Capture stops, the Live Captions become its Transcript; nothing is sent to Deepgram again (ADR-0004).
_Avoid_: live transcript, interim results, partials

**Library**:
The app's catalogue of every Transcript it has produced, wherever the Transcript file lives. A File's Transcript lives next to that File; a Capture's Transcript lives in the app's own transcripts folder.
_Avoid_: history, archive, database
