"""`Transcriber.exe --self-check [report.json]`: prove a build works on this machine without a GUI.

Checks everything the packaged app relies on: the audio devices, a short loopback capture, the
credential store, the settings directory, and both Deepgram request paths up to authentication
(using a bogus key, so the expected outcome is "rejected", which proves the network stack,
certificates and the SDK are all bundled). Writes a JSON report and exits 0 if all checks pass.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import wave
from pathlib import Path


def run_self_check(report_path: str | None) -> int:
    results: dict[str, dict] = {}

    def check(name: str, fn) -> None:
        try:
            results[name] = {"ok": True, "detail": fn()}
        except Exception as e:  # noqa: BLE001 - every failure is a finding
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    check("python", lambda: {"version": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable})
    check("settings_dir", _settings_dir)
    check("devices", _devices)
    check("loopback_capture", _loopback_capture)
    check("credential_store", _credential_store)
    check("deepgram_prerecorded_auth", _deepgram_prerecorded)
    check("deepgram_live_auth", _deepgram_live)

    ok = all(r["ok"] for r in results.values())
    report = {"ok": ok, "checks": results}
    text = json.dumps(report, indent=2)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if ok else 1


def _settings_dir() -> dict:
    from platformdirs import user_data_dir

    from .gui.app import APP_NAME

    return {"data_dir": user_data_dir(APP_NAME, appauthor=False)}


def _devices() -> dict:
    from .audio.wasapi import WasapiCapture
    from .core.capture import DeviceKind

    cap = WasapiCapture()
    devices = cap.list_devices()
    mic = cap.default_device(DeviceKind.INPUT)
    loop = cap.default_device(DeviceKind.LOOPBACK)
    return {
        "count": len(devices),
        "default_microphone": mic.name if mic else None,
        "default_loopback": loop.name if loop else None,
    }


def _loopback_capture() -> dict:
    from .audio.wasapi import WasapiCapture
    from .core.capture import DeviceKind, Recorder

    cap = WasapiCapture()
    loop = cap.default_device(DeviceKind.LOOPBACK)
    if loop is None:
        raise RuntimeError("no loopback device")
    path = Path(tempfile.mkdtemp()) / "loopback.wav"
    rec = Recorder(path)
    handle = cap.open(loop, rec.write)
    time.sleep(0.6)
    handle.stop()
    rec.close()
    with wave.open(str(path), "rb") as w:
        seconds = w.getnframes() / w.getframerate()
    path.unlink()
    if seconds < 0.3:
        raise RuntimeError(f"captured only {seconds:.2f} s")
    return {"seconds": round(seconds, 2)}


def _credential_store() -> dict:
    from .gui.app import build_credentials

    store = build_credentials()
    return {"store": type(store).__name__, "has_key": bool(store.get_api_key())}


def _deepgram_prerecorded() -> dict:
    from .core.gateway import RejectedKeyError
    from .deepgram_gateway import DeepgramGateway

    path = Path(tempfile.mkdtemp()) / "silence.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    try:
        DeepgramGateway("self-check-bogus-key").transcribe(path)
    except RejectedKeyError as e:
        return {"outcome": e.message}
    finally:
        path.unlink()
    raise RuntimeError("Deepgram accepted a bogus key")


def _deepgram_live() -> dict:
    from .core.gateway import RejectedKeyError
    from .deepgram_gateway import DeepgramGateway

    try:
        DeepgramGateway("self-check-bogus-key").open_stream(lambda c: None, lambda e: None)
    except RejectedKeyError as e:
        return {"outcome": e.message}
    raise RuntimeError("Deepgram accepted a bogus key")
