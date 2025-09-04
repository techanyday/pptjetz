logger = logging.getLogger("transcriber")

import os
import time
import logging
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

    # If no English captions, try translating any available transcript to English
    if not selected:
        for t in transcripts:
            try:
                t_en = t.translate("en")
                selected = t_en
                break
            except Exception:
                continue

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
    """Download bestaudio with yt-dlp and convert to 16k mono WAV via ffmpeg.
    Returns path to the WAV file. Raises RuntimeError on failure.
    """
    use_exe = shutil.which("yt-dlp") is not None
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not installed in PATH")

    tmpdir = Path(tempfile.mkdtemp(prefix=f"pptjet_{yt_id}_"))
    # Let yt-dlp decide the correct extension; we will detect the produced file.
    out_tmpl = tmpdir \
        / f"{yt_id}.%(ext)s"

    # Optional: Use browser-exported cookies if provided to bypass YouTube bot/age/region gates
    cookies_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    cookies_args: list[str] = []
    if cookies_file and Path(cookies_file).exists():
        # Render Secret Files are mounted read-only under /etc/secrets. yt-dlp may attempt to
        # update cookies, which fails with Errno 30. Copy to a writable temp path first.
        try:
            tmp_cookies = tmpdir / "cookies.txt"
            # Use binary read/write to preserve bytes accurately
            with open(cookies_file, "rb") as src, open(tmp_cookies, "wb") as dst:
                dst.write(src.read())
            cookies_args = ["--cookies", str(tmp_cookies)]
        except Exception:
            # If copy fails, fall back to original path (best effort read-only)
            cookies_args = ["--cookies", cookies_file]

    wav = tmpdir / f"{yt_id}.wav"

    if use_exe:
        ytdlp_cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",  # fall back to best if separate audio not available (e.g., some Shorts)
            "--no-playlist", "--no-keyring", "--restrict-filenames", "--force-overwrites",
            *cookies_args,
            "-o", str(out_tmpl), url,
        ]
    else:
        # Fallback to Python module invocation (works when installed in venv)
        ytdlp_cmd = [
            shutil.which("python") or "python", "-m", "yt_dlp",
            "-f", "bestaudio/best",
            "--no-playlist", "--no-keyring", "--restrict-filenames", "--force-overwrites",
            *cookies_args,
            "-o", str(out_tmpl), url,
        ]
    r1 = _run(ytdlp_cmd)
    # Detect the produced media file (audio or video). We expect exactly one matching file.
    produced: Optional[Path] = None
    try:
        matches = list(tmpdir.glob(f"{yt_id}.*"))
        # Exclude our target wav in case of retries
        matches = [p for p in matches if p.name != wav.name]
        if matches:
            # Prefer typical audio containers first
            prefer = {"m4a": 0, "webm": 1, "mp3": 2, "mp4": 3}
            matches.sort(key=lambda p: prefer.get(p.suffix.lstrip('.'), 99))
            produced = matches[0]
    except Exception:
        produced = None

    if r1.returncode != 0 or not produced or not produced.exists():
        err = r1.stderr.decode(errors='ignore')
        hint = ""
        if ("Sign in to confirm you're not a bot" in err or "Sign in to confirm you’re not a bot" in err) and not cookies_args:
            hint = (
                " | Requires login cookies. Export a Netscape cookies.txt from your browser and set "
                "YTDLP_COOKIES_FILE to its path (Render Secret File recommended)."
            )
        raise RuntimeError(f"yt-dlp failed: {err[:200]}{hint}")

    ffmpeg_cmd = (
        f"ffmpeg -y -i {shlex.quote(str(produced))} -ac 1 -ar 16000 -f wav {shlex.quote(str(wav))}"
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
    """Transcribe audio using either:
      - Hugging Face distilled Whisper (transformers) if TRANSCRIBE_MODEL=hf-distil
      - faster-whisper (default/fallback)

    API contract unchanged: returns List[Segment].
    """

    # Common config
    task_env = os.getenv("WHISPER_TASK", "transcribe").strip().lower()
    task = task_env if task_env in ("transcribe", "translate") else "transcribe"
    chunk_len = float(os.getenv("WHISPER_CHUNK_LEN", "20"))
    beam_size = int(os.getenv("WHISPER_BEAM_SIZE", "1"))

    backend = os.getenv("TRANSCRIBE_MODEL", "faster-whisper").strip().lower()

    if backend == "hf-distil":
        # Try Hugging Face distilled Whisper via transformers
        model_id = os.getenv("HF_WHISPER_MODEL", "distil-whisper/distil-small.en").strip()
        alt_model_id = os.getenv("HF_WHISPER_MODEL_FALLBACK", "distil-whisper/distil-base.en").strip()

        global _HF_PIPELINE_ID, _HF_PIPELINE
        try:
            _HF_PIPELINE
        except NameError:
            _HF_PIPELINE = None  # type: ignore
            _HF_PIPELINE_ID = None  # type: ignore

        if _HF_PIPELINE is None or _HF_PIPELINE_ID not in (model_id, alt_model_id):
            t0 = time.time()
            logger.info(f"Loading HF pipeline: {model_id} (device=cpu)")
            try:
                from transformers import pipeline
                import torch
                hf_id = model_id
                try:
                    _HF_PIPELINE = pipeline(
                        "automatic-speech-recognition",
                        model=hf_id,
                        device="cpu",
                        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                    )
                    _HF_PIPELINE_ID = hf_id
                except Exception:
                    logger.warning(f"Failed to load {model_id}, trying fallback {alt_model_id}")
                    hf_id = alt_model_id
                    from transformers import pipeline as _pipeline
                    import torch as _torch
                    _HF_PIPELINE = _pipeline(
                        "automatic-speech-recognition",
                        model=hf_id,
                        device="cpu",
                        torch_dtype=_torch.float32,
                    )
                    _HF_PIPELINE_ID = hf_id
            except Exception as e:
                logger.error(f"HF pipeline load failed: {e}; falling back to faster-whisper")
                backend = "faster-whisper"
            else:
                logger.info(f"HF pipeline ready ({_HF_PIPELINE_ID}) in {time.time()-t0:.2f}s")

        if backend == "hf-distil" and _HF_PIPELINE is not None:
            # Run transformers pipeline with chunking; language='en'
            t1 = time.time()
            try:
                result = _HF_PIPELINE(
                    str(path),
                    chunk_length_s=chunk_len,
                    return_timestamps=False,
                    generate_kwargs={
                        "task": task,
                        "language": "en",
                        "temperature": 0.0,
                        "beam_size": max(1, beam_size),
                        "best_of": 1,
                        "condition_on_previous_text": False,
                    },
                )
            except Exception as e:
                msg = str(e)
                if "out of memory" in msg.lower() or "cannot allocate" in msg.lower():
                    raise RuntimeError("Transcription OOM (HF): reduce chunk length or switch to faster-whisper int8")
                raise RuntimeError(f"Transcription failed (HF): {e}")

            text = result["text"] if isinstance(result, dict) else str(result)
            text = (text or "").strip()
            logger.info(
                f"HF ASR done in {time.time()-t1:.2f}s | model={_HF_PIPELINE_ID} | backend=hf-distil"
            )
            if not text:
                raise RuntimeError("ASR produced no text")
            return [Segment(0.0, 0.0, text)]

    # Fallback / default: faster-whisper (ctranslate2)
    env_model = os.getenv("WHISPER_MODEL", os.getenv("WHISPER_MODEL_NAME", "base.en"))
    model_name = (model_size or env_model or "base.en").strip()
    env_compute = os.getenv("WHISPER_COMPUTE_TYPE", os.getenv("WHISPER_COMPUTE", "int8"))
    compute = (compute_type or env_compute or "int8").strip()

    # Global model cache
    global _WHISPER_MODEL, _WHISPER_MODEL_NAME, _WHISPER_COMPUTE
    try:
        _WHISPER_MODEL
    except NameError:
        _WHISPER_MODEL = None  # type: ignore
        _WHISPER_MODEL_NAME = None  # type: ignore
        _WHISPER_COMPUTE = None  # type: ignore

    try:
        from faster_whisper import WhisperModel  # local import
    except Exception as e:
        raise RuntimeError(f"faster-whisper not available and HF backend failed: {e}")

    if _WHISPER_MODEL is None or _WHISPER_MODEL_NAME != model_name or _WHISPER_COMPUTE != compute:
        t0 = time.time()
        logger.info(f"Loading faster-whisper(name={model_name}, device=cpu, compute={compute}) ...")
        try:
            _WHISPER_MODEL = WhisperModel(model_name, device="cpu", compute_type=compute)
            _WHISPER_MODEL_NAME = model_name
            _WHISPER_COMPUTE = compute
        except Exception as e:
            msg = str(e)
            if "out of memory" in msg.lower() or "cannot allocate" in msg.lower():
                raise RuntimeError("Model load OOM: try a smaller model or int8 compute_type")
            raise RuntimeError(f"Failed to load model: {e}")
        finally:
            logger.info(f"Model load took {time.time()-t0:.2f}s")

    model = _WHISPER_MODEL
    t1 = time.time()
    try:
        segments, _info = model.transcribe(
            str(path),
            task=task,
            language="en" if (task == "translate" or model_name.endswith(".en")) else None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            chunk_length=chunk_len,
            beam_size=max(1, beam_size),
            condition_on_previous_text=False,
        )
    except Exception as e:
        msg = str(e)
        if "out of memory" in msg.lower() or "cannot allocate" in msg.lower():
            raise RuntimeError("Transcription OOM: reduce chunk length or use a smaller model")
        raise RuntimeError(f"Transcription failed: {e}")

    out: List[Segment] = [Segment(float(s.start), float(s.end), s.text.strip()) for s in segments]
    duration = time.time() - t1
    words = sum(len(seg.text.split()) for seg in out if seg.text)
    logger.info(f"ASR done in {duration:.2f}s | segments={len(out)} | approx_words={words} | backend=faster-whisper")

    if not any(seg.text for seg in out):
        raise RuntimeError("ASR produced no text")
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
