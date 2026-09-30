"""Windows audio capture over WASAPI via pyaudiowpatch (ADR-0001).

Devices are captured at their native rate and channel count, mixed to mono, and resampled to
16 kHz before they reach the core. Loopback devices (System Audio) appear alongside microphones.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyaudiowpatch as pa

from ..core.capture import SAMPLE_RATE, AudioDevice, CaptureError, CaptureHandle, DeviceKind

CHUNK_SECONDS = 0.1


class Resampler:
    """Linear-interpolation resampler that keeps its phase across chunks."""

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE) -> None:
        self.ratio = src_rate / dst_rate
        self._pos = 0.0  # position of the next output sample, in input-sample units, relative to the buffer start
        self._last: float | None = None

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self.ratio == 1.0:
            return samples
        if self._last is not None:
            buf = np.concatenate(([self._last], samples))
        else:
            buf = samples
        last_index = len(buf) - 1
        if last_index < self._pos:
            return np.empty(0, dtype=np.float32)
        n_out = int((last_index - self._pos) / self.ratio) + 1
        positions = self._pos + np.arange(n_out) * self.ratio
        out = np.interp(positions, np.arange(len(buf)), buf).astype(np.float32)
        self._pos = self._pos + n_out * self.ratio - last_index
        self._last = float(buf[-1])
        return out


class _StreamHandle:
    def __init__(self, stream) -> None:
        self._stream = stream

    def stop(self) -> None:
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
        finally:
            self._stream.close()


class WasapiCapture:
    def __init__(self) -> None:
        self._pa = pa.PyAudio()
        self._wasapi = self._pa.get_host_api_info_by_type(pa.paWASAPI)

    def list_devices(self) -> list[AudioDevice]:
        devices = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info["hostApi"] != self._wasapi["index"] or info["maxInputChannels"] < 1:
                continue
            devices.append(self._to_device(info))
        return devices

    def default_device(self, kind: DeviceKind) -> AudioDevice | None:
        try:
            if kind is DeviceKind.LOOPBACK:
                return self._to_device(self._pa.get_default_wasapi_loopback())
            index = self._wasapi["defaultInputDevice"]
            if index < 0:
                return None
            return self._to_device(self._pa.get_device_info_by_index(index))
        except OSError:
            return None

    def open(self, device: AudioDevice, on_chunk: Callable[[bytes], None]) -> CaptureHandle:
        info = self._pa.get_device_info_by_index(int(device.id))
        rate = int(info["defaultSampleRate"])
        channels = max(1, int(info["maxInputChannels"]))
        resampler = Resampler(rate)

        def callback(in_data, frame_count, time_info, status):
            pcm = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
            if channels > 1:
                pcm = pcm.reshape(-1, channels).mean(axis=1)
            out = resampler.process(pcm)
            if len(out):
                on_chunk(np.clip(out, -32768, 32767).astype(np.int16).tobytes())
            return (None, pa.paContinue)

        try:
            stream = self._pa.open(
                format=pa.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(device.id),
                frames_per_buffer=int(rate * CHUNK_SECONDS),
                stream_callback=callback,
            )
        except OSError as e:
            raise CaptureError(f"Could not open {device.name}: {e}") from e
        stream.start_stream()
        return _StreamHandle(stream)

    @staticmethod
    def _to_device(info) -> AudioDevice:
        kind = DeviceKind.LOOPBACK if info.get("isLoopbackDevice") else DeviceKind.INPUT
        return AudioDevice(id=str(info["index"]), name=info["name"], kind=kind)
