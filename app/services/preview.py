import os
import glob
import uuid
import shutil
import subprocess
import tempfile
import re
import json
import sys
import platform
from typing import List


def _ps_quote(s: str) -> str:
    # Single-quote for PowerShell and escape embedded quotes by doubling.
    return "'" + s.replace("'", "''") + "'"


def _files_all_identical(paths: List[str]) -> bool:
    if len(paths) < 2:
        return False
    try:
        import hashlib

        def _md5(p: str) -> str:
            h = hashlib.md5()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()

        hashes = [_md5(p) for p in paths if os.path.exists(p)]
        return len(hashes) >= 2 and len(set(hashes)) == 1
    except Exception:
        return False


def _export_pngs_via_powerpoint(pptx_path: str, outdir: str, dpi: int, expected_count: int | None) -> List[str]:
    if os.name != "nt":
        return []

    os.makedirs(outdir, exist_ok=True)
    try:
        for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
            try:
                os.remove(fn)
            except Exception:
                pass
    except Exception:
        pass

    pptx_q = _ps_quote(os.path.abspath(pptx_path))
    out_q = _ps_quote(os.path.abspath(outdir))

    # PowerPoint COM export: render each slide to PNG. Requires Microsoft PowerPoint.
    # Use WithWindow = $false to keep it headless.
    script = (
        "$ErrorActionPreference='Stop';"
        f"$pptPath={pptx_q};"
        f"$outDir={out_q};"
        f"$dpi={[int(dpi)]};"
        "$pp=$null; $pres=$null;"
        "try {"
        "  $pp = New-Object -ComObject PowerPoint.Application;"
        "  $pres = $pp.Presentations.Open($pptPath, $true, $true, $false);"
        "  $w = [int][math]::Round(($pres.PageSetup.SlideWidth/72.0) * $dpi);"
        "  $h = [int][math]::Round(($pres.PageSetup.SlideHeight/72.0) * $dpi);"
        "  $i = 1;"
        "  foreach ($s in $pres.Slides) {"
        "    $dst = Join-Path $outDir (\"slide_{0}.png\" -f $i);"
        "    $s.Export($dst, 'PNG', $w, $h) | Out-Null;"
        "    $i++;"
        "  }"
        "} finally {"
        "  if ($pres -ne $null) { try { $pres.Close() } catch {} };"
        "  if ($pp -ne $null) { try { $pp.Quit() } catch {} };"
        "  if ($pres -ne $null) { try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pres) | Out-Null } catch {} };"
        "  if ($pp -ne $null) { try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pp) | Out-Null } catch {} };"
        "  [GC]::Collect(); [GC]::WaitForPendingFinalizers();"
        "}"
    )

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []

    produced = sorted(glob.glob(os.path.join(outdir, "slide_*.png")))
    if expected_count and len(produced) != expected_count:
        return []
    if _files_all_identical(produced):
        return []
    return sorted(produced, key=lambda p: int(re.search(r"slide_(\d+)\.png$", os.path.basename(p)).group(1)))


def _find_soffice() -> str | None:
    # 0) Explicit override via environment variable
    env_path = os.environ.get("LIBREOFFICE_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    # 1) Look in PATH first
    for cand in ("soffice", "libreoffice", "soffice.com", "soffice.exe"):
        path = shutil.which(cand)
        if path:
            return path
    # 1b) Probe common non-Windows install locations
    if os.name != "nt":
        candidates = [
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
            "/usr/local/bin/soffice",
            "/usr/local/bin/libreoffice",
            "/usr/lib/libreoffice/program/soffice",
            "/usr/lib64/libreoffice/program/soffice",
            "/opt/libreoffice/program/soffice",
            "/snap/bin/libreoffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
        for p in candidates:
            try:
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
            except Exception:
                pass
    # 2) Probe common Windows install locations
    candidates = []
    for env_key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env_key)
        if not base:
            continue
        lo_prog = os.path.join(base, "LibreOffice", "program")
        candidates.extend([
            os.path.join(lo_prog, "soffice.com"),
            os.path.join(lo_prog, "soffice.exe"),
        ])
    # Also try default path directly
    candidates.extend([
        r"C:\\Program Files\\LibreOffice\\program\\soffice.com",
        r"C:\\Program Files\\LibreOffice\\program\\soffice.exe",
        r"C:\\Program Files (x86)\\LibreOffice\\program\\soffice.com",
        r"C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe",
    ])
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _soffice_diagnostics() -> dict:
    def _safe_path_val() -> str:
        p = os.environ.get("PATH") or ""
        # Keep enough signal but avoid giant payloads
        return p[:1200]

    env_lo = os.environ.get("LIBREOFFICE_PATH")
    which_map = {}
    for cand in ("soffice", "libreoffice", "soffice.com", "soffice.exe"):
        try:
            which_map[cand] = shutil.which(cand)
        except Exception:
            which_map[cand] = None

    nonwin_candidates = []
    if os.name != "nt":
        for p in (
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
            "/usr/local/bin/soffice",
            "/usr/local/bin/libreoffice",
            "/usr/lib/libreoffice/program/soffice",
            "/usr/lib64/libreoffice/program/soffice",
            "/opt/libreoffice/program/soffice",
            "/snap/bin/libreoffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ):
            try:
                nonwin_candidates.append({
                    "path": p,
                    "exists": bool(os.path.isfile(p)),
                    "x_ok": bool(os.access(p, os.X_OK)) if os.path.exists(p) else False,
                })
            except Exception:
                nonwin_candidates.append({"path": p, "exists": False, "x_ok": False})

    return {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform": platform.platform(),
        "libreoffice_path_env": env_lo,
        "env_path_prefix": _safe_path_val(),
        "which": which_map,
        "non_windows_candidates": nonwin_candidates,
    }


def _soffice_missing_message() -> str:
    is_render = bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_SERVICE_ID")
        or os.environ.get("RENDER_INSTANCE_ID")
    )
    if is_render:
        return (
            "LibreOffice is not installed in this deployment environment. "
            "Ensure the Docker image installs LibreOffice."
        )

    msg = "LibreOffice (soffice) is not installed on the server or not in PATH."
    if os.name == "nt":
        msg += " On Windows, install LibreOffice and ensure 'soffice.com' is available."
        msg += " Typical path: C:\\Program Files\\LibreOffice\\program\\soffice.com"
        msg += " (or set LIBREOFFICE_PATH to the full soffice path)."
    else:
        msg += " Install LibreOffice and ensure 'soffice' is in PATH (or set LIBREOFFICE_PATH)."
    try:
        msg += " Diagnostics: " + json.dumps(_soffice_diagnostics(), ensure_ascii=False)
    except Exception:
        pass
    return msg


def export_slide_pngs(pptx_path: str, outdir: str, dpi: int = 150, expected_count: int | None = None) -> List[str]:
    """
    Export each slide of a PPTX to PNG using LibreOffice (soffice) headless.
    Returns a list of PNG file paths sorted by slide order.
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(_soffice_missing_message())

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
            subprocess.run(cmd_pdf, capture_output=True, text=True)
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
            subprocess.run(cmd_pdf, capture_output=True, text=True)
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
            subprocess.run(cmd_pdf, capture_output=True, text=True)

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
            subprocess.run(cmd_pdf, capture_output=True, text=True)
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
            subprocess.run(cmd_pdf, capture_output=True, text=True)
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
        proc = subprocess.run(cmd, capture_output=True, text=True)
        files = sorted(glob.glob(os.path.join(outdir, "*.png")), key=lambda p: (len(p), p))
        return proc.returncode, files, (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    code, files, logs = run_and_collect(cmd_primary)
    if code != 0 or not files or len(files) == 1:
        code2, files2, logs2 = run_and_collect(cmd_fallback)
        if code2 != 0 and not files2:
            raise RuntimeError(f"LibreOffice export failed. Logs: {logs}\n{logs2}")
        files = files2

    return files


def export_all_previews(pptx_path: str, outdir: str, dpi: int = 170, expected_count: int | None = None) -> List[str]:
    """
    Export every slide to PNG deterministically via:
    PPTX -> PDF (LibreOffice) -> PNG (PyMuPDF)

    Returns list of final file paths (slide_1.png, slide_2.png, ...) in numeric order.
    """
    render_id = uuid.uuid4().hex[:10]
    converter = "soffice_pdf+pymupdf"
    os.makedirs(outdir, exist_ok=True)

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

    cmd_pdf = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        pdf_tmp_dir,
        pptx_path,
    ]
    timeout_s = 0
    try:
        timeout_s = int(os.environ.get("SOFFICE_TIMEOUT_SECONDS") or "90")
    except Exception:
        timeout_s = 90
    try:
        proc = subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"LibreOffice conversion timed out after {timeout_s}s: {exc}")
    soffice_stdout = (proc.stdout or "").strip()
    soffice_stderr = (proc.stderr or "").strip()

    pdf_candidates = []
    try:
        pdf_candidates = [p for p in glob.glob(os.path.join(pdf_tmp_dir, "*")) if os.path.isfile(p) and p.lower().endswith(".pdf")]
    except Exception:
        pdf_candidates = []

    if proc.returncode != 0 or not pdf_candidates:
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
                    "soffice_returncode": proc.returncode,
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
            f"returncode={proc.returncode}. stdout={soffice_stdout[-2000:]} stderr={soffice_stderr[-2000:]}"
        )

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

    doc = None
    final_paths: List[str] = []
    try:
        doc = fitz.open(pdf_path)
        zoom = float(dpi) / 72.0
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
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:
            pass

    if not final_paths:
        raise RuntimeError("No preview images were produced from the rendered PDF.")

    meta = {
        "ok": True,
        "render_id": render_id,
        "converter": converter,
        "pptx_path": pptx_path,
        "pdf_path": pdf_path,
        "outdir": outdir,
        "count": len(final_paths),
        "expected_count": expected_count,
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
