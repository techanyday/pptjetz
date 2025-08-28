import os
import glob
import uuid
import shutil
import subprocess
import tempfile
import re
from typing import List


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


def export_slide_pngs(pptx_path: str, outdir: str, dpi: int = 150) -> List[str]:
    """
    Export each slide of a PPTX to PNG using LibreOffice (soffice) headless.
    Returns a list of PNG file paths sorted by slide order.
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is not installed on the server or not in PATH.")

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
    Export every slide to PNG using LibreOffice with explicit PageRange and Resolution.
    Places files in `outdir` and renames them to slide_<n>.png in numeric order.
    Returns list of final file paths in numeric order.
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is not installed on the server or not in PATH.")

    os.makedirs(outdir, exist_ok=True)

    # LibreOffice filter JSON differs only by shell quoting between platforms.
    # We build a single list form safe for subprocess without shell.
    filter_opt = 'png:impress_png_Export:{"PageRange":"1-999","Resolution":' + str(dpi) + '}'

    cmd = [
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

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # As a fallback, try without explicit filter JSON
        fallback = [
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
        proc2 = subprocess.run(fallback, capture_output=True, text=True)
        if proc2.returncode != 0:
            raise RuntimeError(
                f"LibreOffice export failed.\nPrimary: {proc.stdout}\n{proc.stderr}\nFallback: {proc2.stdout}\n{proc2.stderr}"
            )

    # Collect produced PNGs (names vary by LO; often <basename>_1.png ...)
    produced = sorted(glob.glob(os.path.join(outdir, "*.png")))
    if not produced:
        raise RuntimeError("No PNGs were produced by LibreOffice export.")

    # Normalize names to slide_<n>.png in numeric order
    # Determine order by natural sort on filename numbers
    def _num_key(p: str) -> int:
        base = os.path.basename(p)
        digits = ''.join(ch for ch in base if ch.isdigit())
        try:
            return int(digits) if digits else 0
        except Exception:
            return 0

    produced.sort(key=_num_key)

    # Normalize names using a temporary directory to avoid rename collisions
    tmp_norm = tempfile.mkdtemp(prefix="norm_", dir=outdir)
    norm_paths: List[str] = []
    for i, src in enumerate(produced, start=1):
        tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png")
        try:
            shutil.move(src, tmp_dst)
        except Exception:
            # If move fails, attempt copy as fallback
            try:
                shutil.copy2(src, tmp_dst)
            except Exception:
                continue
        norm_paths.append(tmp_dst)

    final_paths: List[str] = []
    for p in norm_paths:
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
    # Attempt to remove temp dir (ignore errors)
    try:
        shutil.rmtree(tmp_norm, ignore_errors=True)
    except Exception:
        pass

    # If we have an expected count and LO produced fewer, try PDF->PNG fallback
    if expected_count and len(final_paths) < expected_count:
        try:
            # Convert PPTX to PDF
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
                # Use pdftoppm if available
                ppm = shutil.which("pdftoppm")
                if ppm:
                    # Clean any existing slide_*.png to avoid mix
                    for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                        try:
                            os.remove(fn)
                        except Exception:
                            pass
                    # Generate PNGs
                    # -png, -r dpi, -f 1 -l expected_count
                    subprocess.run([
                        ppm,
                        "-png",
                        "-r", str(dpi),
                        "-f", "1",
                        "-l", str(expected_count),
                        pdf_path,
                        os.path.join(outdir, "slide")
                    ], capture_output=True, text=True)
                    # pdftoppm names slide-1.png, slide-2.png ... rename to slide_1.png (collision-safe)
                    pdf_pngs = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                    def _pdf_num(p: str) -> int:
                        base = os.path.basename(p)
                        digits = ''.join(ch for ch in base if ch.isdigit())
                        try:
                            return int(digits)
                        except Exception:
                            return 0
                    pdf_pngs.sort(key=_pdf_num)
                    # Normalize via temp dir to avoid rename collisions
                    tmp_norm2 = tempfile.mkdtemp(prefix="norm_pdf_", dir=outdir)
                    moved: List[str] = []
                    for i, src in enumerate(pdf_pngs, start=1):
                        tmp_dst = os.path.join(tmp_norm2, f"slide_{i}.png")
                        try:
                            shutil.move(src, tmp_dst)
                            moved.append(tmp_dst)
                        except Exception:
                            pass
                    final_paths = []
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
                        shutil.rmtree(tmp_norm2, ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            # Ignore fallback failure; return what we have
            pass

    # If multiple PNGs exist but appear identical (common Windows LO quirk), try PDF->PNG to regenerate
    if expected_count and len(final_paths) > 1:
        try:
            import hashlib
            def _md5(p: str) -> str:
                h = hashlib.md5()
                with open(p, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        h.update(chunk)
                return h.hexdigest()
            hashes = [_md5(p) for p in final_paths if os.path.exists(p)]
            unique = set(hashes)
            if len(unique) == 1:
                # Detected duplicates; try PDF pipeline
                print("[preview] Detected identical PNG thumbnails; retrying via PDF->PNG pipeline")
                for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                    try:
                        os.remove(fn)
                    except Exception:
                        pass
                pdf_tmp = os.path.join(outdir, "__pages.pdf")
                try:
                    for fn in glob.glob(os.path.join(outdir, "*.pdf")):
                        try:
                            os.remove(fn)
                        except Exception:
                            pass
                except Exception:
                    pass
                cmd_pdf = [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                    outdir,
                    pptx_path,
                ]
                subprocess.run(cmd_pdf, capture_output=True, text=True)
                pdf_candidates = glob.glob(os.path.join(outdir, "*.pdf"))
                if pdf_candidates:
                    src_pdf = max(pdf_candidates, key=lambda p: os.path.getmtime(p))
                    try:
                        if os.path.abspath(src_pdf) != os.path.abspath(pdf_tmp):
                            os.replace(src_pdf, pdf_tmp)
                        else:
                            pdf_tmp = src_pdf
                    except Exception:
                        pdf_tmp = src_pdf
                if os.path.exists(pdf_tmp):
                    try:
                        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf_tmp, os.path.join(outdir, "slide")], capture_output=True, text=True)
                        produced = sorted(glob.glob(os.path.join(outdir, "slide-*.png")))
                        for p in produced:
                            m = re.search(r"slide-(\d+)\.png$", p)
                            if not m:
                                continue
                            i = int(m.group(1))
                            dst = os.path.join(outdir, f"slide_{i}.png")
                            try:
                                if os.path.exists(dst):
                                    os.remove(dst)
                            except Exception:
                                pass
                            try:
                                os.replace(p, dst)
                                final_paths.append(dst)
                            except Exception:
                                pass
                    except FileNotFoundError:
                        # pdftoppm not installed; try PyMuPDF (fitz)
                        try:
                            import fitz  # PyMuPDF
                            doc = fitz.open(pdf_tmp)
                            zoom = dpi / 72.0
                            mat = fitz.Matrix(zoom, zoom)
                            new_paths: List[str] = []
                            for i in range(len(doc)):
                                pix = doc.load_page(i).get_pixmap(matrix=mat)
                                dst = os.path.join(outdir, f"slide_{i+1}.png")
                                if os.path.exists(dst):
                                    try:
                                        os.remove(dst)
                                    except Exception:
                                        pass
                                pix.save(dst)
                                new_paths.append(dst)
                            doc.close()
                            if new_paths:
                                final_paths = new_paths
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            pass

    # As a last resort (Windows LO sometimes outputs only first page), try per-slide loop
    if expected_count and len(final_paths) < expected_count:
        try:
            # Remove any existing slide_*.png to avoid mixing
            for fn in glob.glob(os.path.join(outdir, "slide_*.png")):
                try:
                    os.remove(fn)
                except Exception:
                    pass

            def _export_single(page_n: int) -> str | None:
                # Export one page to a unique temp directory to avoid collisions/overwrites
                tmp_dir = os.path.join(outdir, f"__tmp_page_{page_n}")
                os.makedirs(tmp_dir, exist_ok=True)
                filter_one = f'png:impress_png_Export:{{"PageRange":"{page_n}","Resolution":{dpi}}}'
                cmd_one = [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--convert-to",
                    filter_one,
                    "--outdir",
                    tmp_dir,
                    pptx_path,
                ]
                subprocess.run(cmd_one, capture_output=True, text=True)
                # Find PNG inside tmp_dir
                candidates = glob.glob(os.path.join(tmp_dir, "*.png"))
                if not candidates:
                    # Cleanup temp dir
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
                    return None
                # Choose the first (or newest) PNG
                newest = max(candidates, key=lambda p: os.path.getmtime(p))
                dst = os.path.join(outdir, f"slide_{page_n}.png")
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except Exception:
                        pass
                try:
                    os.replace(newest, dst)
                except Exception:
                    dst = None
                # Cleanup temp dir
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
                return dst if dst and os.path.exists(dst) else None

            per_paths: List[str] = []
            for i in range(1, expected_count + 1):
                got = _export_single(i)
                if got:
                    per_paths.append(got)
                else:
                    break
            if per_paths:
                final_paths = per_paths
        except Exception:
            pass

    return final_paths
