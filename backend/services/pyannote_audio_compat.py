"""Compatibility surface for pyannote.audio on recent torchaudio releases."""

from functools import wraps
from numbers import Integral
from threading import RLock
from typing import NamedTuple


class AudioMetaData(NamedTuple):
    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


_INSTALL_LOCK = RLock()
_WRAPPER_MARKER = "_voicestudio_pyannote_soundfile_adapter"


def _sample_width(subtype: str) -> int:
    for prefix in ("PCM_", "DWVW_", "DPCM_", "ALAC_"):
        if subtype.startswith(prefix) and subtype[len(prefix):].isdigit():
            return int(subtype[len(prefix):])
    if subtype in {"PCM_U8", "PCM_S8", "ULAW", "ALAW"}:
        return 8
    if subtype == "FLOAT":
        return 32
    if subtype == "DOUBLE":
        return 64
    return 0


def _legacy_encoding(container: str, subtype: str) -> str:
    if container == "FLAC":
        return "FLAC"
    if subtype == "PCM_U8":
        return "PCM_U"
    if subtype.startswith("PCM_"):
        return "PCM_S"
    if subtype in {"FLOAT", "DOUBLE"}:
        return "PCM_F"
    if subtype in {"ULAW", "ALAW", "VORBIS"}:
        return subtype
    return "UNKNOWN"


def _soundfile_info(uri, format=None, buffer_size=4096, backend=None):
    del format, buffer_size
    if backend not in (None, "soundfile"):
        raise ValueError(f"Audio metadata backend {backend!r} is not supported by this adapter")

    import soundfile

    details = soundfile.info(uri)
    subtype = details.subtype or ""
    return AudioMetaData(
        int(details.samplerate),
        int(details.frames),
        int(details.channels),
        _sample_width(subtype),
        _legacy_encoding(details.format or "", subtype),
    )


def _soundfile_load(
    uri,
    frame_offset=0,
    num_frames=-1,
    normalize=True,
    channels_first=True,
    format=None,
    buffer_size=4096,
    backend=None,
):
    del format, buffer_size
    if backend not in (None, "soundfile"):
        raise ValueError(f"Audio decoding backend {backend!r} is not supported by this adapter")
    if not isinstance(frame_offset, Integral) or frame_offset < 0:
        raise ValueError("frame_offset must be a non-negative integer")
    if not isinstance(num_frames, Integral) or num_frames < -1:
        raise ValueError("num_frames must be -1 or a non-negative integer")

    import soundfile
    import torch

    with soundfile.SoundFile(uri) as stream:
        subtype = stream.subtype or ""
        native_wav = stream.format == "WAV" and not normalize
        if native_wav:
            dtype_by_subtype = {
                "PCM_16": "int16",
                "PCM_24": "int32",
                "PCM_32": "int32",
                "FLOAT": "float32",
                "DOUBLE": "float64",
                "PCM_U8": "int16",
            }
            try:
                read_dtype = dtype_by_subtype[subtype]
            except KeyError as exc:
                raise ValueError(f"Unnormalized WAV subtype {subtype!r} is not supported") from exc
        else:
            read_dtype = "float32"

        start = min(int(frame_offset), int(stream.frames))
        if stream.seekable() or start:
            stream.seek(start)
        remaining = int(stream.frames) - start
        count = remaining if num_frames == -1 else min(int(num_frames), remaining)
        samples = stream.read(frames=count, dtype=read_dtype, always_2d=True)
        sample_rate = int(stream.samplerate)

    waveform = torch.from_numpy(samples)
    if native_wav and subtype == "PCM_U8":
        waveform = ((waveform.to(torch.int32) >> 8) + 128).to(torch.uint8)
    if channels_first:
        waveform = waveform.transpose(0, 1)
    return waveform, sample_rate


def _backends():
    return ["soundfile"]


def ensure_pyannote_audio_compat() -> bool:
    """Install the legacy audio entry points needed by pyannote.audio 3.x."""
    import torchaudio

    with _INSTALL_LOCK:
        legacy_names = ("AudioMetaData", "info", "list_audio_backends")
        if all(hasattr(torchaudio, name) for name in legacy_names):
            return False

        import soundfile

        if not callable(getattr(soundfile, "SoundFile", None)):
            raise TypeError("The pyannote audio adapter needs a callable soundfile.SoundFile")
        current_load = getattr(torchaudio, "load", None)
        if not callable(current_load):
            raise TypeError("The torchaudio loader is unavailable to the pyannote audio adapter")

        if not getattr(current_load, _WRAPPER_MARKER, False):
            original_load = current_load

            @wraps(original_load)
            def compatible_load(*args, **kwargs):
                requested_backend = kwargs.get("backend")
                if "backend" not in kwargs and len(args) > 7:
                    requested_backend = args[7]
                if requested_backend == "soundfile":
                    return _soundfile_load(*args, **kwargs)
                return original_load(*args, **kwargs)

            setattr(compatible_load, _WRAPPER_MARKER, True)
            torchaudio.load = compatible_load

        replacements = {
            "AudioMetaData": AudioMetaData,
            "info": _soundfile_info,
            "list_audio_backends": _backends,
        }
        for name, replacement in replacements.items():
            if not hasattr(torchaudio, name):
                setattr(torchaudio, name, replacement)
        return True
