import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

# External libs (declared in requirements)
try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
except Exception:  # pragma: no cover
    YouTubeTranscriptApi = None  # type: ignore
    TranscriptsDisabled = Exception  # type: ignore
    NoTranscriptFound = Exception  # type: ignore


def parse_youtube_id(url: str) -> Optional[str]:
    """Extract the 11-char YouTube video ID from various URL formats.
    Returns None if not found.
    """
    if not url:
        return None
    url = url.strip()
    # Common patterns: watch?v=ID, youtu.be/ID, shorts/ID, embed/ID
    patterns = [
        r"(?:v=|vi=)([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"/shorts/([a-zA-Z0-9_-]{11})",
        r"/embed/([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def get_captions(yt_id: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch official human/auto English captions via youtube-transcript-api.
    Returns list of {start, end, text} or None if unavailable.
    """
    if not YouTubeTranscriptApi:
        return None
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(yt_id)
    except (TranscriptsDisabled, NoTranscriptFound, Exception):
        return None

    # Prefer human English, else auto-generated English
    order = [
        (lambda t: not t.is_generated and t.language_code.startswith("en")),
        (lambda t: t.is_generated and t.language_code.startswith("en")),
    ]
    selected = None
    for pred in order:
        for t in transcripts:
            try:
                if pred(t):
                    selected = t
                    break
            except Exception:
                continue
        if selected:
            break

    if not selected:
        return None

    try:
        items = selected.fetch()
        # Normalize to {start, end, text}
        out: List[Dict[str, Any]] = []
        for it in items:
            start = float(it.get("start", 0.0))
            dur = float(it.get("duration", 0.0))
            text = str(it.get("text", ""))
            out.append({"start": start, "end": start + dur, "text": text})
        return out
    except Exception:
        return None


def _run(cmd: str | list[str]) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = cmd
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def download_audio_wav(url: str, yt_id: str) -> Path:
    """Download bestaudio with yt-dlp (Python API) and convert to 16k mono WAV via ffmpeg.
    Returns path to the WAV file. Raises RuntimeError on failure.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not installed in PATH")

    tmpdir = Path(tempfile.mkdtemp(prefix=f"pptjet_{yt_id}_"))
    wav = tmpdir / f"{yt_id}.wav"

    # Use yt_dlp Python API for more control and reliability (nsig, UA, shorts)
    try:
        import yt_dlp  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"yt-dlp import failed: {e}")

    outtmpl = str(tmpdir / f"{yt_id}.%(ext)s")
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    # Always canonicalize to watch URL to avoid shorts/embed quirks
    canonical_url = f"https://www.youtube.com/watch?v={yt_id}"

    # First pass: normal extraction
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[acodec^=opus]/bestaudio/best",
        "noplaylist": True,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": ua, "Referer": "https://www.youtube.com/"},
        "retries": 3,
        "fragment_retries": 3,
        "geo_bypass": True,
        "nocheckcertificate": True,
    }

    downloaded_path: Optional[Path] = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(canonical_url, download=True)
            # Try to resolve the actual downloaded filename
            if info:
                # When outtmpl uses %(ext)s, the final filename is predictable
                # but we don't know the extension. Find the first matching file.
                for p in tmpdir.iterdir():
                    if p.is_file() and p.stem.startswith(yt_id) and p.suffix.lower() != ".wav":
                        downloaded_path = p
                        break
    except Exception as e:
        # Attempt a cache clear and second pass using Android client to bypass signature/403
        try:
            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                cache = ydl.cache
                if cache:
                    cache.remove()
        except Exception:
            pass
        # Second pass opts
        ydl_opts2 = dict(ydl_opts)
        ydl_opts2.update({
            "extractor_args": {
                "youtube": {
                    "player_client": ["android"],
                }
            },
            # Looser format chain
            "format": "bestaudio/best",
        })
        try:
            with yt_dlp.YoutubeDL(ydl_opts2) as ydl:
                info = ydl.extract_info(canonical_url, download=True)
                for p in tmpdir.iterdir():
                    if p.is_file() and p.stem.startswith(yt_id) and p.suffix.lower() != ".wav":
                        downloaded_path = p
                        break
        except Exception as e2:
            # Third pass: try with cookies from local browser to bypass 403/age/region restrictions
            try:
                ydl_opts3 = dict(ydl_opts2)
                ydl_opts3.update({
                    # Try common Windows browsers; yt-dlp will pick what's available
                    "cookiesfrombrowser": ("edge", True, True),
                })
                with yt_dlp.YoutubeDL(ydl_opts3) as ydl:
                    info = ydl.extract_info(canonical_url, download=True)
                    for p in tmpdir.iterdir():
                        if p.is_file() and p.stem.startswith(yt_id) and p.suffix.lower() != ".wav":
                            downloaded_path = p
                            break
            except Exception as e3:
                # Last attempt: try Chrome
                try:
                    ydl_opts4 = dict(ydl_opts2)
                    ydl_opts4.update({
                        "cookiesfrombrowser": ("chrome", True, True),
                    })
                    with yt_dlp.YoutubeDL(ydl_opts4) as ydl:
                        info = ydl.extract_info(canonical_url, download=True)
                        for p in tmpdir.iterdir():
                            if p.is_file() and p.stem.startswith(yt_id) and p.suffix.lower() != ".wav":
                                downloaded_path = p
                                break
                except Exception as e4:
                    raise RuntimeError(f"yt-dlp failed: {str(e2)[:120]} | {str(e3)[:120]} | {str(e4)[:120]}") from e2

    if not downloaded_path or not downloaded_path.exists():
        raise RuntimeError("yt-dlp failed to produce an audio file")

    ffmpeg_cmd = (
        f"ffmpeg -y -i {shlex.quote(str(downloaded_path))} -ac 1 -ar 16000 -f wav {shlex.quote(str(wav))}"
    )
    r2 = _run(ffmpeg_cmd)
    if r2.returncode != 0 or not wav.exists():
        raise RuntimeError(f"ffmpeg failed: {r2.stderr.decode(errors='ignore')[:200]}")

    return wav


@dataclass
class Segment:
    start: float
    end: float
    text: str


def transcribe_wav(path: Path, model_size: Optional[str] = None, compute_type: Optional[str] = None) -> List[Segment]:
    """Transcribe using faster-whisper with light VAD filtering. Returns punctuated segments.

    Robust to unsupported compute types: if float16 is requested but not supported (e.g., CPU-only),
    we automatically retry with int8.
    """
    model_size = model_size or os.getenv("WHISPER_MODEL", "small")
    compute_type = (compute_type or os.getenv("WHISPER_COMPUTE", "int8")).strip()
    threads_env = os.getenv("WHISPER_THREADS", "").strip()
    cpu_threads = None
    try:
        if threads_env:
            cpu_threads = max(1, int(threads_env))
    except Exception:
        cpu_threads = None

    from faster_whisper import WhisperModel  # import here so module loads without pkg

    def make_model(ct: str) -> Any:
        # cpu_threads only applies on CPU; passing None lets the lib default
        return WhisperModel(model_size, device="auto", compute_type=ct, cpu_threads=cpu_threads)

    try:
        model = make_model(compute_type)
    except ValueError as e:
        # Typical message: "Requested float16 compute type, but the target device or backend do not support efficient float16 computation."
        if "float16" in str(e).lower():
            model = make_model("int8")
        else:
            raise

    segments, _info = model.transcribe(
        str(path), vad_filter=True, vad_parameters={"min_silence_duration_ms": 300}
    )
    out: List[Segment] = []
    for seg in segments:
        out.append(Segment(float(seg.start), float(seg.end), seg.text.strip()))
    return out


def join_segments(segments: List[Dict[str, Any] | Segment]) -> str:
    """Merge segments into clean paragraphs (~80–120 words) and strip artifacts."""
    def normalize_text(t: str) -> str:
        t = re.sub(r"\s+", " ", t)
        # Remove common caption artifacts
        t = re.sub(r"\[(?:Applause|Music|Laughter|Silence)\]", "", t, flags=re.I)
        t = t.replace("♪", "")
        return t.strip()

    words: List[str] = []
    for s in segments:
        text = s.text if isinstance(s, Segment) else str(s.get("text", ""))
        if text:
            words.extend(normalize_text(text).split())

    # Build paragraphs of 80-120 words.
    paras: List[str] = []
    cur: List[str] = []
    for w in words:
        cur.append(w)
        if len(cur) >= 100:  # middle ground between 80-120
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))

    return "\n\n".join(paras)
