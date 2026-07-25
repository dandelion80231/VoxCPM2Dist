# torchaudio_shim.py
#
# Restores the legacy torchaudio.load/save/info API (the `backend='soundfile'`
# keyword) on top of `soundfile` (libsndfile). Needed because this environment
# has torchaudio 2.11, which removed the backend parameter and routes all I/O
# through torchcodec (whose ffmpeg DLL is missing here). The CosyVoice3 /
# IndexTTS2 codebases call `torchaudio.load(..., backend='soundfile')`, i.e.
# they already intend the soundfile backend, so delegating to soundfile is the
# semantically correct fix.
#
# Apply at the very top of a script (before importing cosyvoice / indextts):
#     import torchaudio_shim, torchaudio
#     torchaudio.load = torchaudio_shim.load
#     torchaudio.save = torchaudio_shim.save
#     torchaudio.info = torchaudio_shim.info
import io
import numpy as np
import soundfile as sf
import torch


def load(uri, out=None, backend="soundfile", channels_first=True,
         frame_offset=0, num_frames=-1, seek_offset=0, **kwargs):
    """Mirror torchaudio.load: returns (waveform[channels, time] float32, sr)."""
    if isinstance(uri, (bytes, bytearray)):
        uri = io.BytesIO(uri)
    data, sr = sf.read(uri, dtype="float32", always_2d=False)
    # soundfile: (n,) mono or (n, c) multi-channel -> torchaudio (c, n)
    if data.ndim == 1:
        data = data[None, :]
    elif not channels_first:
        pass  # leave as (n, c)
    else:
        data = data.T
    if frame_offset > 0 or num_frames not in (-1, 0, None):
        start = frame_offset
        end = None if num_frames in (-1, 0, None) else frame_offset + num_frames
        data = data[:, start:end]
    return torch.from_numpy(np.ascontiguousarray(data)).float(), sr


def save(uri, tensor, sample_rate, backend="soundfile",
         bits_per_sample=None, encoding=None, **kwargs):
    """Mirror torchaudio.save: tensor is (channels, time)."""
    arr = tensor.detach().cpu().numpy() if hasattr(tensor, "detach") else np.asarray(tensor)
    if arr.ndim == 1:
        arr = arr[None, :]
    else:
        arr = arr.T  # (c, t) -> (t, c)
    if np.issubdtype(arr.dtype, np.integer):
        subtype = "PCM_16"
        arr = arr.astype("int16")
    else:
        subtype = "FLOAT"
        arr = arr.astype("float32")
    sf.write(uri, arr, sample_rate, subtype=subtype, format="WAV")


class _Info:
    def __init__(self, sr, frames, channels):
        self.sample_rate = sr
        self.num_frames = frames
        self.num_channels = channels
        self.bits_per_sample = 16
        self.encoding = "PCM_16"

    def __repr__(self):
        return f"AudioInfo(sample_rate={self.sample_rate}, num_frames={self.num_frames}, num_channels={self.num_channels})"


def info(uri, backend="soundfile", **kwargs):
    f = sf.info(uri)
    return _Info(f.samplerate, f.frames, f.channels)
