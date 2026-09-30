# System Audio capture sits behind a platform interface, Windows first via pyaudiowpatch

Capturing "what the computer is playing" (a Zoom or Teams call) has no portable API: Windows exposes WASAPI loopback, Linux exposes PulseAudio/PipeWire monitor devices, and macOS has nothing built in (it needs a virtual audio driver such as BlackHole, or ScreenCaptureKit, which has no mature Python binding). We keep all System Audio capture behind one small interface and ship only a Windows implementation, using `pyaudiowpatch`, a PortAudio fork whose purpose is WASAPI loopback and which has a Python 3.14 wheel. Everything else in the app (PySide6, the Deepgram SDK, keyring, microphone capture, PyInstaller) is already cross-platform, so adding macOS or Linux later means one new implementation of this interface and nothing more.

## Considered Options

- `sounddevice`: the usual PortAudio binding, but its bundled PortAudio exposes no loopback devices on Windows at all, so it cannot capture System Audio.
- `soundcard`: pure Python, does loopback on Windows and Linux with one API. Rejected for now because `pyaudiowpatch` is more widely used on Windows and `soundcard` has a known bug when recording a single channel. It is the natural choice if Linux becomes a target.

## Consequences

- The app is Windows-only until another implementation exists.
- Capture is per output device, not per application. Anything else playing through the same device (music, notifications) is transcribed too. No Python library offers per-app capture on Windows.
