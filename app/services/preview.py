import glob
import json
import logging
import os
import pathlib
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


def _find_soffice() -> str | None:
    env = (os.environ.get("SOFFICE_PATH") or os.environ.get("LIBREOFFICE_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env

    exe = shutil.which("soffice") or shutil.which("soffice.exe")
    if exe:
        return exe

    # Common locations
    candidates: List[str] = []
    if os.name == "nt":
        candidates.extend([
            r"C:\\Program Files\\LibreOffice\\program\\soffice.exe",
            r"C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe",
        ])
    else:
        candidates.extend([
            "/usr/bin/soffice",
            "/usr/local/bin/soffice",
            "/snap/bin/libreoffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ])

    for p in candidates:
        try:
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


def _soffice_missing_message() -> str:
    return (
        "LibreOffice (soffice) is required to generate slide previews. "
        "Install LibreOffice and ensure `soffice` is on PATH, "
        "or set SOFFICE_PATH to the full executable path."
    )


def _soffice_diagnostics() -> Dict[str, Any]:
    soffice = None
    try:
        soffice = _find_soffice()
    except Exception:
        soffice = None

    out: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version,
        "os_name": os.name,
        "soffice_found": bool(soffice),
        "soffice_path": soffice,
        "SOFFICE_PATH": os.environ.get("SOFFICE_PATH"),
        "SOFFICE_TIMEOUT_SECONDS": os.environ.get("SOFFICE_TIMEOUT_SECONDS"),
        "LIBREOFFICE_TIMEOUT": os.environ.get("LIBREOFFICE_TIMEOUT"),
    }
    return out


def _file_uri(path: str) -> str:
    try:
        return pathlib.Path(path).resolve().as_uri()
    except Exception:
        p = str(path or "").replace("\\", "/")
        if p.startswith("/"):
            return "file://" + p
        return "file:///" + p


def _is_pdf_cache_valid(*, pptx_path: str, pdf_path: str) -> bool:
    try:
        if not (pptx_path and pdf_path):
            return False
        if not os.path.isfile(pdf_path):
            return False
        if not os.path.isfile(pptx_path):
            return False
        if os.path.getsize(pdf_path) <= 0:
            return False
        return os.path.getmtime(pdf_path) >= os.path.getmtime(pptx_path)
    except Exception:
        return False


def _kill_soffice_process(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
            return
        except Exception:
            pass

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_soffice_convert(cmd: List[str], *, timeout_s: int, env: Dict[str, str] | None) -> tuple[int, str, str, int]:
    t0 = time.perf_counter()
    popen_kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": env,
    }
    if os.name == "nt":
        try:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=float(timeout_s))
    except subprocess.TimeoutExpired:
        _kill_soffice_process(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        dur_ms = int((time.perf_counter() - t0) * 1000)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s, output=stdout, stderr=stderr)
    dur_ms = int((time.perf_counter() - t0) * 1000)
    return int(proc.returncode or 0), (stdout or ""), (stderr or ""), dur_ms

def export_slide_pngs(pptx_path: str, outdir: str, dpi: int = 150, expected_count: int | None = None) -> List[str]:
    """
    Export each slide of a PPTX to PNG using LibreOffice (soffice) headless.
    Returns a list of PNG file paths sorted by slide order.
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(_soffice_missing_message())

    try:
        logger.info("preview:pptx_to_pdf start pptx=%s outdir=%s", pptx_path, outdir)
    except Exception:
        pass

    timeout_s = 0
    try:
        timeout_s = int(os.environ.get("SOFFICE_TIMEOUT_SECONDS") or "60")
    except Exception:
        timeout_s = 60

    os.makedirs(outdir, exist_ok=True)

    # Windows-first: attempt PPTX->PDF + PDF->PNG to avoid LO repeating slide 1
    if os.name == "nt":
        try:
            # Clean previous PNGs
            for pat in ("slide_*.png", "slide-*.png"):
                for fn in glob.glob(os.path.join(outdir, pat)):
                    try:
                        os.remove(fn)
                    except Exception:
                        pass
            pdf_path = os.path.join(outdir, "deck.pdf")
            cmd_pdf = [
                soffice,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                pptx_path,
            ]
            try:
                subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")
            early_paths: List[str] = []
            if os.path.isfile(pdf_path):
                ppm = shutil.which("pdftoppm")
                if ppm:
                    subprocess.run([ppm, "-png", "-r", str(dpi), pdf_path, os.path.join(outdir, "slide")], capture_output=True, text=True)
                    produced = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                    def _num(p: str) -> int:
                        base = os.path.basename(p)
                        digits = ''.join(ch for ch in base if ch.isdigit())
                        try:
                            return int(digits)
                        except Exception:
                            return 0
                    produced.sort(key=_num)
                    tmp_norm = tempfile.mkdtemp(prefix="norm_win_early_", dir=outdir)
                    moved: List[str] = []
                    for i, src in enumerate(produced, start=1):
                        tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png")
                        try:
                            shutil.move(src, tmp_dst)
                            moved.append(tmp_dst)
                        except Exception:
                            pass
                    for p in moved:
                        dst = os.path.join(outdir, os.path.basename(p))
                        try:
                            if os.path.exists(dst):
                                os.remove(dst)
                        except Exception:
                            pass
                        try:
                            shutil.move(p, dst)
                            early_paths.append(dst)
                        except Exception:
                            pass
                    try:
                        shutil.rmtree(tmp_norm, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    try:
                        import fitz  # PyMuPDF
                        doc = fitz.open(pdf_path)
                        zoom = dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        for i in range(len(doc)):
                            pix = doc.load_page(i).get_pixmap(matrix=mat)
                            dst = os.path.join(outdir, f"slide_{i+1}.png")
                            if os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                            pix.save(dst)
                            early_paths.append(dst)
                        doc.close()
                    except Exception:
                        early_paths = []

            # If we have multiple images (or match expected_count) and they are not identical, return early
            if early_paths and (not expected_count or len(early_paths) == expected_count or len(early_paths) > 1):
                try:
                    import hashlib
                    hashes = []
                    for p in early_paths:
                        h = hashlib.md5()
                        with open(p, 'rb') as f:
                            for chunk in iter(lambda: f.read(8192), b''):
                                h.update(chunk)
                        hashes.append(h.hexdigest())
                    if len(set(hashes)) > 1:
                        return early_paths
                except Exception:
                    return early_paths
        except Exception:
            # Ignore and continue with LibreOffice PNG route
            pass

    # Windows-first: try PPTX->PDF then PDF->PNG to avoid LO identical-slide bug
    if os.name == "nt":
        try:
            # Clean any old outputs
            for pat in ("slide_*.png", "slide-*.png"):
                for fn in glob.glob(os.path.join(outdir, pat)):
                    try:
                        os.remove(fn)
                    except Exception:
                        pass
            pdf_path = os.path.join(outdir, "deck.pdf")
            cmd_pdf = [
                soffice,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                pptx_path,
            ]
            try:
                subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")
            final_paths_win: List[str] = []
            if os.path.isfile(pdf_path):
                ppm = shutil.which("pdftoppm")
                if ppm:
                    subprocess.run([
                        ppm, "-png", "-r", str(dpi), pdf_path, os.path.join(outdir, "slide")
                    ], capture_output=True, text=True)
                    produced = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                    def _num(p: str) -> int:
                        base = os.path.basename(p)
                        digits = ''.join(ch for ch in base if ch.isdigit())
                        try:
                            return int(digits)
                        except Exception:
                            return 0
                    produced.sort(key=_num)
                    tmp_norm = tempfile.mkdtemp(prefix="norm_win_pre_", dir=outdir)
                    moved: List[str] = []
                    for i, src in enumerate(produced, start=1):
                        tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png")
                        try:
                            shutil.move(src, tmp_dst)
                            moved.append(tmp_dst)
                        except Exception:
                            pass
                    for p in moved:
                        dst = os.path.join(outdir, os.path.basename(p))
                        try:
                            if os.path.exists(dst):
                                os.remove(dst)
                        except Exception:
                            pass
                        try:
                            shutil.move(p, dst)
                            final_paths_win.append(dst)
                        except Exception:
                            pass
                    try:
                        shutil.rmtree(tmp_norm, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    try:
                        import fitz
                        doc = fitz.open(pdf_path)
                        zoom = dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        for i in range(len(doc)):
                            pix = doc.load_page(i).get_pixmap(matrix=mat)
                            dst = os.path.join(outdir, f"slide_{i+1}.png")
                            if os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                            pix.save(dst)
                            final_paths_win.append(dst)
                        doc.close()
                    except Exception:
                        final_paths_win = []
            if final_paths_win and (not expected_count or len(final_paths_win) == expected_count or len(final_paths_win) > 1):
                # Ensure they are not identical before returning
                try:
                    import hashlib
                    hset = set()
                    for p in final_paths_win:
                        h = hashlib.md5()
                        with open(p, 'rb') as f:
                            for chunk in iter(lambda: f.read(8192), b''):
                                h.update(chunk)
                        hset.add(h.hexdigest())
                    if len(hset) > 1:
                        return final_paths_win
                except Exception:
                    return final_paths_win
        except Exception:
            # Fall through to LO PNG path
            pass

    # Windows-first: Prefer PPTX->PDF (LibreOffice) then PDF->PNG (PyMuPDF) to
    # avoid the LO bug that produces identical thumbnails for all slides.
    if os.name == "nt":
        try:
            # Clean old previews
            for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                try:
                    os.remove(fn)
                except Exception:
                    pass

            # Convert PPTX -> PDF using soffice
            pdf_path = os.path.join(outdir, "deck.pdf")
            cmd_pdf = [
                soffice,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                pptx_path,
            ]
            try:
                subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")

            final_paths_win: List[str] = []
            if os.path.isfile(pdf_path):
                try:
                    import fitz  # PyMuPDF
                    doc = fitz.open(pdf_path)
                    zoom = dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    for i in range(len(doc)):
                        pix = doc.load_page(i).get_pixmap(matrix=mat)
                        dst = os.path.join(outdir, f"slide_{i+1}.png")
                        if os.path.exists(dst):
                            try:
                                os.remove(dst)
                            except Exception:
                                pass
                        pix.save(dst)
                        final_paths_win.append(dst)
                    doc.close()
                except Exception:
                    final_paths_win = []

            # If we successfully rendered multiple pages (or matched expected_count), return them.
            if final_paths_win and (not expected_count or len(final_paths_win) == expected_count or len(final_paths_win) > 1):
                try:
                    # Sanity: ensure not all files are identical
                    import hashlib
                    hashes = []
                    for p in final_paths_win:
                        h = hashlib.md5()
                        with open(p, 'rb') as f:
                            for chunk in iter(lambda: f.read(8192), b''):
                                h.update(chunk)
                        hashes.append(h.hexdigest())
                    if len(set(hashes)) > 1:
                        return final_paths_win
                except Exception:
                    # If hashing fails, still return what we have
                    return final_paths_win
        except Exception:
            # Fall through to LibreOffice PNG path
            pass

    # Windows-first: use PDF->PNG to avoid LO identical-slide quirk
    if os.name == "nt":
        try:
            # Clean existing slide_*.png
            for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                try:
                    os.remove(fn)
                except Exception:
                    pass
            # Export PPTX->PDF
            pdf_path = os.path.join(outdir, "deck.pdf")
            cmd_pdf = [
                soffice,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                pptx_path,
            ]
            try:
                subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")
            final_paths_win: List[str] = []
            if os.path.isfile(pdf_path):
                ppm = shutil.which("pdftoppm")
                if ppm:
                    subprocess.run([
                        ppm,
                        "-png",
                        "-r", str(dpi),
                        pdf_path,
                        os.path.join(outdir, "slide")
                    ], capture_output=True, text=True)
                    pdf_pngs = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                    def _pdf_num(p: str) -> int:
                        base = os.path.basename(p)
                        digits = ''.join(ch for ch in base if ch.isdigit())
                        try:
                            return int(digits)
                        except Exception:
                            return 0
                    pdf_pngs.sort(key=_pdf_num)
                    tmp_norm = tempfile.mkdtemp(prefix="norm_win_pdf_", dir=outdir)
                    moved: List[str] = []
                    for i, src in enumerate(pdf_pngs, start=1):
                        tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png")
                        try:
                            shutil.move(src, tmp_dst)
                            moved.append(tmp_dst)
                        except Exception:
                            pass
                    for p in moved:
                        dst = os.path.join(outdir, os.path.basename(p))
                        try:
                            if os.path.exists(dst):
                                os.remove(dst)
                        except Exception:
                            pass
                        try:
                            shutil.move(p, dst)
                            final_paths_win.append(dst)
                        except Exception:
                            pass
                    try:
                        shutil.rmtree(tmp_norm, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    try:
                        import fitz  # PyMuPDF
                        doc = fitz.open(pdf_path)
                        zoom = dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        for i in range(len(doc)):
                            pix = doc.load_page(i).get_pixmap(matrix=mat)
                            dst = os.path.join(outdir, f"slide_{i+1}.png")
                            if os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                            pix.save(dst)
                            final_paths_win.append(dst)
                        doc.close()
                    except Exception:
                        final_paths_win = []
            if final_paths_win and (not expected_count or len(final_paths_win) == expected_count or len(final_paths_win) > 1):
                # Ensure not all images are identical; if identical, fall through to LO path
                try:
                    import hashlib
                    def _md5(p: str) -> str:
                        h = hashlib.md5()
                        with open(p, 'rb') as f:
                            for chunk in iter(lambda: f.read(8192), b''):
                                h.update(chunk)
                        return h.hexdigest()
                    hashes = [_md5(p) for p in final_paths_win if os.path.exists(p)]
                    if len(set(hashes)) > 1:
                        return final_paths_win
                except Exception:
                    # If hashing fails, still return what we have
                    return final_paths_win
        except Exception:
            pass

    # On Windows, prefer PDF->PNG first to avoid LO bug that repeats slide 1 imagery
    # If successful, return early.
    if os.name == "nt":
        try:
            # Clean existing slide_*.png
            for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                try:
                    os.remove(fn)
                except Exception:
                    pass
            # Export PPTX->PDF
            pdf_path = os.path.join(outdir, "deck.pdf")
            cmd_pdf = [
                soffice,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                pptx_path,
            ]
            try:
                subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")
            if os.path.isfile(pdf_path):
                ppm = shutil.which("pdftoppm")
                final_paths: List[str] = []
                if ppm:
                    subprocess.run([
                        ppm,
                        "-png",
                        "-r", str(dpi),
                        pdf_path,
                        os.path.join(outdir, "slide")
                    ], capture_output=True, text=True)
                    # Normalize slide-*.png -> slide_*.png
                    pdf_pngs = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                    def _pdf_num(p: str) -> int:
                        base = os.path.basename(p)
                        digits = ''.join(ch for ch in base if ch.isdigit())
                        try:
                            return int(digits)
                        except Exception:
                            return 0
                    pdf_pngs.sort(key=_pdf_num)
                    tmp_norm = tempfile.mkdtemp(prefix="norm_pdf_", dir=outdir)
                    moved: List[str] = []
                    for i, src in enumerate(pdf_pngs, start=1):
                        tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png")
                        try:
                            shutil.move(src, tmp_dst)
                            moved.append(tmp_dst)
                        except Exception:
                            pass
                    for p in moved:
                        dst = os.path.join(outdir, os.path.basename(p))
                        try:
                            if os.path.exists(dst):
                                os.remove(dst)
                        except Exception:
                            pass
                        try:
                            shutil.move(p, dst)
                            final_paths.append(dst)
                        except Exception:
                            pass
                    try:
                        shutil.rmtree(tmp_norm, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    # PyMuPDF fallback
                    try:
                        import fitz
                        doc = fitz.open(pdf_path)
                        zoom = dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        for i in range(len(doc)):
                            pix = doc.load_page(i).get_pixmap(matrix=mat)
                            dst = os.path.join(outdir, f"slide_{i+1}.png")
                            if os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                            pix.save(dst)
                            final_paths.append(dst)
                        doc.close()
                    except Exception:
                        final_paths = []
                # Early return if we produced any slides (and preferably match expected_count)
                if final_paths and (not expected_count or len(final_paths) == expected_count or len(final_paths) > 1):
                    return final_paths
        except Exception:
            # Ignore and continue with LO PNG path
            pass

    # Clean any old normalized previews to avoid stale files mixing in
    try:
        for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
            try:
                os.remove(fn)
            except Exception:
                pass
    except Exception:
        pass
    # Primary: plain png conversion (often exports all slides reliably)
    cmd_primary = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        "png",
        "--outdir",
        outdir,
        pptx_path,
    ]
    # Fallback: explicit filter with PageRange and Resolution
    filter_opt = 'png:impress_png_Export:{"PageRange":"1-999","Resolution":' + str(dpi) + '}'
    cmd_fallback = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        filter_opt,
        "--outdir",
        outdir,
        pptx_path,
    ]

    def run_and_collect(cmd):
        timeout_s = 0
        try:
            timeout_s = int(os.environ.get("SOFFICE_TIMEOUT_SECONDS") or "60")
        except Exception:
            timeout_s = 60
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        files = sorted(glob.glob(os.path.join(outdir, "*.png")), key=lambda p: (len(p), p))
        return proc.returncode, files, (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    code, files, logs = run_and_collect(cmd_primary)
    if code != 0 or not files or len(files) == 1:
        code2, files2, logs2 = run_and_collect(cmd_fallback)
        if code2 != 0 and not files2:
            raise RuntimeError(f"LibreOffice export failed. Logs: {logs}\n{logs2}")
        files = files2

    return files


def export_all_previews(pptx_path: str, outdir: str, dpi: int = 170, expected_count: int | None = None, max_pages: int | None = None) -> List[str]:
    """
    Export every slide to PNG deterministically via:
    PPTX -> PDF (LibreOffice) -> PNG (PyMuPDF)

    Returns list of final file paths (slide_1.png, slide_2.png, ...) in numeric order.
    """
    render_id = uuid.uuid4().hex[:10]
    converter = "soffice_pdf+pymupdf"
    os.makedirs(outdir, exist_ok=True)

    t0 = None
    try:
        t0 = time.perf_counter()
    except Exception:
        t0 = None

    # Clear old PNG previews (case-insensitive)
    try:
        for fn in glob.glob(os.path.join(outdir, "*")):
            if os.path.isfile(fn) and fn.lower().endswith(".png"):
                try:
                    os.remove(fn)
                except Exception:
                    pass
    except Exception:
        pass

    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(_soffice_missing_message())

    # 1) PPTX -> PDF
    pdf_path = os.path.join(outdir, "deck.pdf")
    if _is_pdf_cache_valid(pptx_path=pptx_path, pdf_path=pdf_path):
        try:
            logger.info("preview:pptx_to_pdf cache_hit pptx=%s pdf=%s", pptx_path, pdf_path)
        except Exception:
            pass
    else:
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass

    pdf_tmp_dir = None
    try:
        pdf_tmp_dir = tempfile.mkdtemp(prefix=f"pdf_{render_id}_", dir=outdir)
    except Exception:
        pdf_tmp_dir = outdir

    LIBREOFFICE_TIMEOUT = 0
    try:
        LIBREOFFICE_TIMEOUT = int(os.getenv("LIBREOFFICE_TIMEOUT", 180))
    except Exception:
        LIBREOFFICE_TIMEOUT = 180
    env = None
    try:
        env = dict(os.environ)
        safe_home = os.path.join(outdir, "_home")
        safe_tmp = os.path.join(outdir, "_tmp")
        os.makedirs(safe_home, exist_ok=True)
        os.makedirs(safe_tmp, exist_ok=True)
        env["HOME"] = safe_home
        env["TMPDIR"] = safe_tmp
        env["TMP"] = safe_tmp
        env["TEMP"] = safe_tmp
        if os.name == "nt":
            env["USERPROFILE"] = safe_home
    except Exception:
        env = None

    proc_code = 0
    soffice_stdout = ""
    soffice_stderr = ""
    dur_ms = 0

    if not _is_pdf_cache_valid(pptx_path=pptx_path, pdf_path=pdf_path):
        last_cmd: List[str] = []
        profile_dirs: List[str] = []
        pptx_size = None
        try:
            pptx_size = os.path.getsize(pptx_path)
        except Exception:
            pptx_size = None
        for attempt in (1, 2):
            unique_id = f"{render_id}_{uuid.uuid4().hex[:8]}_a{attempt}"
            profile_dir = os.path.join(tempfile.gettempdir(), f"libreoffice_profile_{unique_id}")
            profile_dirs.append(profile_dir)
            try:
                os.makedirs(profile_dir, exist_ok=True)
            except Exception:
                pass

            profile_arg = f"-env:UserInstallation={_file_uri(profile_dir)}"
            cmd_pdf = [
                soffice,
                profile_arg,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                pdf_tmp_dir,
                pptx_path,
            ]
            last_cmd = cmd_pdf

            try:
                logger.info(
                    "preview:pptx_to_pdf run attempt=%s timeout_s=%s pptx=%s pptx_bytes=%s outdir=%s tmpdir=%s profile=%s",
                    attempt,
                    LIBREOFFICE_TIMEOUT,
                    pptx_path,
                    pptx_size,
                    outdir,
                    pdf_tmp_dir,
                    profile_dir,
                )
            except Exception:
                pass

            try:
                proc_code, stdout, stderr, dur_ms = _run_soffice_convert(cmd_pdf, timeout_s=LIBREOFFICE_TIMEOUT, env=env)
                soffice_stdout = (stdout or "").strip()
                soffice_stderr = (stderr or "").strip()
            except subprocess.TimeoutExpired as exc:
                soffice_stdout = str(getattr(exc, "output", "") or "").strip()
                soffice_stderr = str(getattr(exc, "stderr", "") or "").strip()
                try:
                    logger.error(
                        "preview:pptx_to_pdf timeout attempt=%s timeout_s=%s cmd=%s stdout=%s stderr=%s",
                        attempt,
                        LIBREOFFICE_TIMEOUT,
                        cmd_pdf,
                        (soffice_stdout or "")[-2000:],
                        (soffice_stderr or "")[-2000:],
                    )
                except Exception:
                    pass
                if attempt == 1:
                    continue
                raise RuntimeError(f"LibreOffice conversion timed out after {LIBREOFFICE_TIMEOUT}s: {exc}")
            except Exception as exc:
                try:
                    logger.error(
                        "preview:pptx_to_pdf failed attempt=%s timeout_s=%s cmd=%s dur_ms=%s stdout=%s stderr=%s",
                        attempt,
                        LIBREOFFICE_TIMEOUT,
                        cmd_pdf,
                        dur_ms,
                        (soffice_stdout or "")[-2000:],
                        (soffice_stderr or "")[-2000:],
                    )
                except Exception:
                    pass
                if attempt == 1:
                    continue
                raise

            pdf_candidates = []
            try:
                pdf_candidates = [p for p in glob.glob(os.path.join(pdf_tmp_dir, "*")) if os.path.isfile(p) and p.lower().endswith(".pdf")]
            except Exception:
                pdf_candidates = []

            if proc_code == 0 and pdf_candidates:
                break
            try:
                logger.error(
                    "preview:pptx_to_pdf nonzero attempt=%s returncode=%s cmd=%s dur_ms=%s stdout=%s stderr=%s candidates=%s",
                    attempt,
                    proc_code,
                    cmd_pdf,
                    dur_ms,
                    (soffice_stdout or "")[-2000:],
                    (soffice_stderr or "")[-2000:],
                    len(pdf_candidates),
                )
            except Exception:
                pass
            if attempt == 1:
                continue
            raise RuntimeError(
                "Failed to convert PPTX to PDF via LibreOffice. "
                f"returncode={proc_code}. stdout={soffice_stdout[-2000:]} stderr={soffice_stderr[-2000:]}"
            )

        for d in profile_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    pdf_candidates = []
    if _is_pdf_cache_valid(pptx_path=pptx_path, pdf_path=pdf_path):
        pdf_candidates = [pdf_path]
    else:
        try:
            pdf_candidates = [p for p in glob.glob(os.path.join(pdf_tmp_dir, "*")) if os.path.isfile(p) and p.lower().endswith(".pdf")]
        except Exception:
            pdf_candidates = []

    if (not _is_pdf_cache_valid(pptx_path=pptx_path, pdf_path=pdf_path)) and (proc_code != 0 or not pdf_candidates):
        try:
            logger.error(
                "preview:pptx_to_pdf final_fail timeout_s=%s dur_ms=%s pptx=%s pptx_bytes=%s pdf_path=%s tmpdir=%s outdir=%s cmd=%s",
                LIBREOFFICE_TIMEOUT,
                dur_ms,
                pptx_path,
                (pptx_size if 'pptx_size' in locals() else None),
                pdf_path,
                pdf_tmp_dir,
                outdir,
                (locals().get('last_cmd') or []),
            )
        except Exception:
            pass
        # Optional last-resort on Windows: PowerPoint COM to PNG (if available)
        if os.name == "nt":
            ppt_paths = _export_pngs_via_powerpoint(pptx_path, outdir, dpi=dpi, expected_count=expected_count)
            if ppt_paths:
                meta = {
                    "ok": True,
                    "render_id": render_id,
                    "converter": "powerpoint_com",
                    "pptx_path": pptx_path,
                    "pdf_path": None,
                    "outdir": outdir,
                    "count": len(ppt_paths),
                    "expected_count": expected_count,
                    "soffice_returncode": proc_code,
                    "soffice_stdout": soffice_stdout[-4000:],
                    "soffice_stderr": soffice_stderr[-4000:],
                }
                try:
                    with open(os.path.join(outdir, "_meta.json"), "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False)
                except Exception:
                    pass
                return ppt_paths

        raise RuntimeError(
            "Failed to convert PPTX to PDF via LibreOffice. "
            f"returncode={proc_code}. stdout={soffice_stdout[-2000:]} stderr={soffice_stderr[-2000:]}"
        )

    try:
        logger.info("preview:pptx_to_pdf done returncode=%s candidates=%s dur_ms=%s", proc_code, len(pdf_candidates), dur_ms)
    except Exception:
        pass

    try:
        if t0 is not None:
            logger.info("preview:stage pptx_to_pdf_ms=%s", int((time.perf_counter() - t0) * 1000))
    except Exception:
        pass

    src_pdf = max(pdf_candidates, key=lambda p: os.path.getmtime(p))
    try:
        if os.path.abspath(src_pdf) != os.path.abspath(pdf_path):
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            shutil.move(src_pdf, pdf_path)
    except Exception:
        pdf_path = src_pdf

    # Cleanup temp PDF dir if it was separate
    try:
        if pdf_tmp_dir and os.path.abspath(pdf_tmp_dir) != os.path.abspath(outdir):
            shutil.rmtree(pdf_tmp_dir, ignore_errors=True)
    except Exception:
        pass

    # 2) PDF -> PNG (PyMuPDF)
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"PyMuPDF is required for preview rendering but is not available: {exc}")

    try:
        logger.info("preview:pdf_to_png start pdf=%s dpi=%s", pdf_path, dpi)
    except Exception:
        pass

    doc = None
    final_paths: List[str] = []
    pdf_timeout_s = 0
    try:
        pdf_timeout_s = int(os.environ.get("PYMUPDF_TIMEOUT_SECONDS") or "60")
    except Exception:
        pdf_timeout_s = 60
    t_pdf = None
    try:
        t_pdf = time.perf_counter()
    except Exception:
        t_pdf = None
    try:
        doc = fitz.open(pdf_path)
        zoom = float(dpi) / 72.0
        mat = fitz.Matrix(zoom, zoom)
        total_pages = len(doc)
        limit_pages = total_pages
        if isinstance(max_pages, int) and max_pages > 0:
            limit_pages = min(limit_pages, max_pages)
        for i in range(limit_pages):
            if t_pdf is not None and (time.perf_counter() - t_pdf) > float(pdf_timeout_s):
                raise RuntimeError(f"PDF->PNG conversion exceeded {pdf_timeout_s}s (rendered {i}/{limit_pages} pages)")
            pix = doc.load_page(i).get_pixmap(matrix=mat)
            dst = os.path.join(outdir, f"slide_{i+1}.png")
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except Exception:
                    pass
            pix.save(dst)
            final_paths.append(dst)
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:
            pass

    if not final_paths:
        raise RuntimeError("No preview images were produced from the rendered PDF.")

    try:
        logger.info("preview:pdf_to_png done count=%s", len(final_paths))
    except Exception:
        pass

    try:
        if t_pdf is not None:
            logger.info("preview:stage pdf_to_png_ms=%s", int((time.perf_counter() - t_pdf) * 1000))
    except Exception:
        pass

    meta = {
        "ok": True,
        "render_id": render_id,
        "converter": converter,
        "pptx_path": pptx_path,
        "pdf_path": pdf_path,
        "outdir": outdir,
        "count": len(final_paths),
        "expected_count": expected_count,
        "max_pages": max_pages,
        "soffice_returncode": proc.returncode,
        "soffice_stdout": soffice_stdout[-4000:],
        "soffice_stderr": soffice_stderr[-4000:],
    }
    try:
        with open(os.path.join(outdir, "_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass

    # Validate but do not hard-fail on mismatch
    if expected_count and expected_count != len(final_paths):
        print(f"[preview] Warning: expected {expected_count} slides but rendered {len(final_paths)} pages")

    return final_paths
