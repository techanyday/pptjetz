from __future__ import annotations
def _clean_bullets(items):
    """Return list of non-empty, trimmed strings."""
    return [str(x).strip() for x in (items or []) if str(x).strip()]

def _ensure_min_points(pts, min_points=4, agenda_fallback=None):
    """Ensure at least min_points items by topping up from agenda then 'N/A'."""
    cleaned = _clean_bullets(pts)
    afb = _clean_bullets(agenda_fallback or [])
    while len(cleaned) < min_points and afb:
        cleaned.append(afb.pop(0))
    while len(cleaned) < min_points:
        cleaned.append("N/A")
    return cleaned
"""Google Slides API helper for PPTJetX.

This module replaces the old python-pptx‐based PPTGenerator.  It builds a
presentations.batchUpdate JSON payload for an 8-slide deck and optionally calls
Google Slides API endpoints to create the presentation and export it as a PPTX
file.

At the moment we only construct and return the batchUpdate payload – the actual
API call is left to the caller so that authentication details can live in the
Flask route (service account / OAuth flow).  This keeps the helper framework-
agnostic and easy to unit-test.
"""
 

import os
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from google.oauth2 import service_account  # type: ignore
from google.oauth2.credentials import Credentials  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.http import MediaIoBaseDownload  # type: ignore
import io

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    # Allows creating and editing files the app creates.
    "https://www.googleapis.com/auth/drive",  # full Drive access for service-account

]

# ---------------------------------------------------------------------------
# Visual theme defaults
# ---------------------------------------------------------------------------
DEFAULT_THEME = {
    "background": "#F8F9FA",
    "primary": "#0077B6",     # headings
    "secondary": "#48CAE4",
    "accent": "#90E0EF",
    "text": "#212529",        # body text
    "font": "Inter",
    "title_size": 40,
    "subtitle_size": 28,
    "body_size": 20,
}

# Slide ordering (must remain 8 slides)
SLIDE_KINDS_ORDER = [
    "title",
    "intro",
    "timeline",  # conditional
    "quote",
    "deep_dive",
    "infographic",
    "conclusion",
    "qa",
]

# ---------------------------------------------------------------------------
# Helper class
# ---------------------------------------------------------------------------

class SlidesAPIHelper:  # pylint: disable=too-few-public-methods
    """Lightweight wrapper around Google Slides API for PPTJetX.

    If *creds* (an instance of ``google.oauth2.credentials.Credentials``) is
    supplied we use it directly.  Otherwise we fall back to loading a service-
    account key pointed to by *credentials_json_path* (or the relevant env
    vars).  This keeps backward-compatibility while allowing per-user OAuth
    flows.
    """

    def __init__(
        self,
        creds: Credentials | None = None,
        *,
        credentials_json_path: str | None = None,
    ) -> None:
        if creds is None:
            cred_path = (
                credentials_json_path
                or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            )
            if not cred_path:
                raise RuntimeError(
                    "Credentials not supplied: either pass a Credentials object or set GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS."
                )
            creds = service_account.Credentials.from_service_account_file(cred_path, scopes=SCOPES)
        # build clients
        self.slides_service = build("slides", "v1", credentials=creds, cache_discovery=False)
        self.drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # -----------------------------
    # Public helpers
    # -----------------------------

    def create_presentation(
        self,
        title: str,
        requests: List[Dict[str, Any]],
        *,
        export: bool = True,
    ) -> Tuple[str, str | None]:
        """Create a new presentation, apply *requests* via batchUpdate.

        Returns `(presentation_id, export_link)` where *export_link* may be
        ``None`` if *export* is ``False``.
        """
        # Validate requests iterable
        if not isinstance(requests, list):
            raise ValueError("Requests payload is not a list; upstream build_batch_update likely failed.")

        presentation = self.slides_service.presentations().create(body={"title": title}).execute()
        presentation_id = presentation["presentationId"]

        # Apply batchUpdate
        if requests:
            # Delete the default first slide created by Google so our custom
            # first slide appears as slide 1 and not slide 2.
            try:
                first_slide_id = presentation.get("slides", [{}])[0].get("objectId")
            except Exception:  # noqa: BLE001
                first_slide_id = None
            combined = []
            if first_slide_id:
                combined.append({"deleteObject": {"objectId": first_slide_id}})
            combined.extend(requests)
            self.slides_service.presentations().batchUpdate(
                presentationId=presentation_id, body={"requests": combined}
            ).execute()

        export_link: str | None = None
        if export:
            mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            export_link = (
                f"https://slides.googleapis.com/v1/presentations/{presentation_id}:export?mimeType={mime_type}"
            )
        return presentation_id, export_link

    # -----------------------------
    # Template-driven generation
    # -----------------------------

    def copy_from_template(self, template_file_id: str, *, new_title: str) -> str:
        """Duplicate a Google Slides file via Drive API and return new presentationId."""
        copied = self.drive_service.files().copy(fileId=template_file_id, body={"name": new_title}).execute()
        return copied["id"]

    def _get_presentation(self, presentation_id: str) -> Dict[str, Any]:
        return self.slides_service.presentations().get(presentationId=presentation_id).execute()

    @staticmethod
    def _collect_placeholders(slide: Dict[str, Any]) -> Dict[str, list[str]]:
        out: Dict[str, list[str]] = {"TITLE": [], "SUBTITLE": [], "BODY": []}
        for el in slide.get("pageElements") or []:
            shp = el.get("shape") or {}
            ph = shp.get("placeholder") or {}
            ph_type = ph.get("type")
            # Normalize common centered variants to standard keys
            if ph_type == "CENTERED_TITLE":
                ph_type = "TITLE"
            elif ph_type == "CENTERED_BODY":
                ph_type = "BODY"
            if ph_type in out and el.get("objectId"):
                out[ph_type].append(el["objectId"])
        return out

    @staticmethod
    def _pick_placeholder(slide: Dict[str, Any], candidates: list[str]) -> str | None:
        """Pick the most likely content placeholder among candidates on a slide.

        Heuristic: select by largest area, break ties by closest to page center.
        """
        if not candidates:
            return None
        # Gather geometry for candidates
        scored: list[tuple[float, str]] = []  # (score, objectId)
        # Page default size (points). Slides default is 960x540; use as center.
        page_w = 960.0
        page_h = 540.0
        try:
            if slide.get("pageProperties", {}).get("pageSize", {}).get("width", {}).get("magnitude"):
                page_w = float(slide["pageProperties"]["pageSize"]["width"]["magnitude"])
            if slide.get("pageProperties", {}).get("pageSize", {}).get("height", {}).get("magnitude"):
                page_h = float(slide["pageProperties"]["pageSize"]["height"]["magnitude"])
        except Exception:
            pass
        cx, cy = page_w / 2.0, page_h / 2.0
        # Map elements by id for quick lookup
        by_id = {el.get("objectId"): el for el in (slide.get("pageElements") or [])}
        for oid in candidates:
            el = by_id.get(oid) or {}
            size = (el.get("size") or {})
            w = float((size.get("width") or {}).get("magnitude") or 0)
            h = float((size.get("height") or {}).get("magnitude") or 0)
            area = w * h
            tr = (el.get("transform") or {})
            x = float(tr.get("translateX") or 0) + w / 2.0
            y = float(tr.get("translateY") or 0) + h / 2.0
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            # Higher score for larger area, penalize distance
            score = area - dist * 10.0
            scored.append((score, oid))
        scored.sort(reverse=True)
        return scored[0][1] if scored else candidates[0]

    @staticmethod
    def _pick_title_slide_subtitle(slide: Dict[str, Any], candidates: list[str]) -> str | None:
        """For the title slide, prefer the bottom-centered subtitle placeholder.
        Score by larger area, then by higher translateY (lower on page), then X proximity to center.
        """
        if not candidates:
            return None
        page_w = 960.0
        try:
            if slide.get("pageProperties", {}).get("pageSize", {}).get("width", {}).get("magnitude"):
                page_w = float(slide["pageProperties"]["pageSize"]["width"]["magnitude"])
        except Exception:
            pass
        cx = page_w / 2.0
        by_id = {el.get("objectId"): el for el in (slide.get("pageElements") or [])}
        scored: list[tuple[float, str]] = []
        for oid in candidates:
            el = by_id.get(oid) or {}
            size = (el.get("size") or {})
            w = float((size.get("width") or {}).get("magnitude") or 0)
            h = float((size.get("height") or {}).get("magnitude") or 0)
            area = w * h
            tr = (el.get("transform") or {})
            x = float(tr.get("translateX") or 0) + w / 2.0
            y = float(tr.get("translateY") or 0) + h / 2.0
            # Prefer larger area, then larger y (near bottom), then closeness to center X
            score = area + y * 50.0 - abs(x - cx) * 10.0
            scored.append((score, oid))
        scored.sort(reverse=True)
        return scored[0][1]

    def _text_len(self, presentation: Dict[str, Any], obj_id: str) -> int:
        """Return length of existing text for the shape with objectId=obj_id.
        If the object isn't found or has no text, returns 0.
        """
        try:
            for slide in presentation.get("slides", []) or []:
                for el in (slide.get("pageElements") or []):
                    if el.get("objectId") == obj_id:
                        shp = el.get("shape") or {}
                        txt = (shp.get("text") or {}).get("textElements") or []
                        total = 0
                        for te in txt:
                            run = te.get("textRun") or {}
                            content = run.get("content")
                            if isinstance(content, str):
                                total += len(content)
                        return total
        except Exception:
            return 0
        return 0

    def _clear_and_insert(self, presentation: Dict[str, Any], obj_id: str, text: str) -> list[Dict[str, Any]]:
        reqs: list[Dict[str, Any]] = []
        cur_len = self._text_len(presentation, obj_id)
        if text:
            # Insert first to inherit the first character's style in the placeholder,
            # then delete any remaining old characters after the newly inserted text.
            reqs.append({"insertText": {"objectId": obj_id, "insertionIndex": 0, "text": text}})
            if cur_len > 0:
                # Delete everything after the newly inserted text to the end of the shape
                reqs.append({
                    "deleteText": {
                        "objectId": obj_id,
                        "textRange": {"type": "FROM_START_INDEX", "startIndex": len(text)}
                    }
                })
        else:
            # If we explicitly want to clear to empty, only delete when there is content
            if cur_len > 0:
                reqs.append({"deleteText": {"objectId": obj_id, "textRange": {"type": "ALL"}}})
        return reqs

    def _fill_slide(self, presentation: Dict[str, Any], index: int, *, title: str | None = None, subtitle: str | None = None, bullets: list[str] | None = None, left: dict | None = None, right: dict | None = None, paragraph: str | None = None) -> list[Dict[str, Any]]:
        """Build requests to fill slide N (0-based index) placeholders.
        Strategy:
          - TITLE -> title
          - SUBTITLE -> subtitle or paragraph
          - BODY placeholders -> first BODY gets paragraph or joined bullets; if two BODY boxes and left/right provided, map left->BODY[0], right->BODY[1].
        """
        slides = presentation.get("slides") or []
        if index >= len(slides):
            return []
        slide = slides[index]
        ph = self._collect_placeholders(slide)
        # Diagnostics: print counts per type
        try:
            print(f"[SlidesAPI] Slide {index+1} placeholders -> TITLE={len(ph['TITLE'])} SUBTITLE={len(ph['SUBTITLE'])} BODY={len(ph['BODY'])}")
        except Exception:
            pass
        reqs: list[Dict[str, Any]] = []
        # Title
        if ph["TITLE"] and title is not None:
            tgt_title = self._pick_placeholder(slide, ph["TITLE"]) or ph["TITLE"][0]
            reqs += self._clear_and_insert(presentation, tgt_title, title)
        # Subtitle or paragraph
        if ph["SUBTITLE"]:
            sub_text = subtitle or (paragraph or "")
            if sub_text:
                # On title slide (index==0), pick bottom-centered subtitle if possible
                tgt_sub = None
                try:
                    # index is captured in closure; inspect title location by presence of title placeholder only
                    # Use specialized picker when there are multiple subtitles
                    if len(ph["SUBTITLE"]) > 1:
                        tgt_sub = self._pick_title_slide_subtitle(slide, ph["SUBTITLE"]) or ph["SUBTITLE"][0]
                except Exception:
                    tgt_sub = None
                if not tgt_sub:
                    tgt_sub = self._pick_placeholder(slide, ph["SUBTITLE"]) or ph["SUBTITLE"][0]
                reqs += self._clear_and_insert(presentation, tgt_sub, sub_text)
        # Bodies
        bodies = ph["BODY"]
        if left and right and len(bodies) >= 2:
            # Build column text without empty leading lines to avoid empty bullets
            l_parts = []
            if isinstance(left.get("heading"), str) and left.get("heading").strip():
                l_parts.append(left.get("heading").strip())
            l_points = [str(x).strip() for x in (left.get("points") or []) if str(x).strip()]
            l_parts.extend(l_points)
            ltxt = "\n".join(l_parts)

            r_parts = []
            if isinstance(right.get("heading"), str) and right.get("heading").strip():
                r_parts.append(right.get("heading").strip())
            r_points = [str(x).strip() for x in (right.get("points") or []) if str(x).strip()]
            r_parts.extend(r_points)
            rtxt = "\n".join(r_parts)
            # For two-column layouts, still pick the two largest/most central bodies
            # Sort bodies by area score and map left->first, right->second
            if len(bodies) > 2:
                # Score and sort descending
                sorted_bodies = []
                by_id = {el.get("objectId"): el for el in (slide.get("pageElements") or [])}
                def score(oid: str) -> float:
                    el = by_id.get(oid) or {}
                    size = (el.get("size") or {})
                    w = float((size.get("width") or {}).get("magnitude") or 0)
                    h = float((size.get("height") or {}).get("magnitude") or 0)
                    return w * h
                sorted_bodies = sorted(bodies, key=score, reverse=True)
            else:
                sorted_bodies = bodies
            reqs += self._clear_and_insert(presentation, sorted_bodies[0], ltxt)
            reqs += self._clear_and_insert(presentation, sorted_bodies[1], rtxt)
        else:
            body_text = None
            if paragraph:
                body_text = paragraph
            elif bullets:
                body_text = "\n".join([str(x) for x in bullets])
            # Prefer BODY[0]; if there is no BODY placeholder, fall back to SUBTITLE
            if body_text:
                target_obj = None
                if bodies:
                    target_obj = self._pick_placeholder(slide, bodies) or bodies[0]
                elif ph["SUBTITLE"]:
                    target_obj = self._pick_placeholder(slide, ph["SUBTITLE"]) or ph["SUBTITLE"][0]
                if target_obj:
                    reqs += self._clear_and_insert(presentation, target_obj, body_text)
                    # If bullets provided, ensure bullet styling is applied
                    if bullets and isinstance(bullets, list) and bullets:
                        reqs.append({
                            "createParagraphBullets": {
                                "objectId": target_obj,
                                "textRange": {"type": "ALL"},
                                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                            }
                        })
                else:
                    # No suitable placeholder found; log clearly
                    print(f"[SlidesAPI] Warning: No BODY/SUBTITLE placeholder to target on slide {index+1}; skipping body_text insertion.")
        return reqs

    def create_from_template_positions(
        self,
        *,
        template_file_id: str,
        new_title: str,
        positions: dict[int, dict[str, Any]],  # keys 1..7
    ) -> Tuple[str, str | None]:
        """Copy from template, then fill placeholders slide-by-slide using provided data.
        Returns (presentation_id, export_link)
        """
        pres_id = self.copy_from_template(template_file_id, new_title=new_title)
        pres = self._get_presentation(pres_id)
        requests: list[Dict[str, Any]] = []
        # Fill slides 1..7 (convert to 0-based)
        for pos, data in sorted(positions.items()):
            idx = max(0, pos - 1)
            # For slide 1, if subtitle is empty, fall back to presenter or date
            sub = data.get("subtitle")
            if idx == 0 and (not sub or not isinstance(sub, str) or not sub.strip()):
                sub = data.get("presenter") or data.get("date") or ""

            requests += self._fill_slide(
                pres,
                idx,
                title=data.get("title"),
                subtitle=sub,
                bullets=data.get("bullets"),
                left=data.get("left"),
                right=data.get("right"),
                paragraph=data.get("paragraph"),
            )
        # Slide 2: increase line spacing for readability (targets BODY or SUBTITLE)
        if len(pres.get("slides", [])) >= 2:
            slide2 = pres["slides"][1]
            ph2 = self._collect_placeholders(slide2)
            target = None
            if ph2["BODY"]:
                target = ph2["BODY"][0]
            elif ph2["SUBTITLE"]:
                target = ph2["SUBTITLE"][0]
            if target:
                requests.append({
                    "updateParagraphStyle": {
                        "objectId": target,
                        "textRange": {"type": "ALL"},
                        "style": {"lineSpacing": 120},
                        "fields": "lineSpacing"
                    }
                })
        # Replace default hint strings on slide 1 as a safety net (keeps placeholder styles)
        try:
            first = pres.get("slides", [])[0]
            t = (positions.get(1) or {}).get("title") or ""
            s = (positions.get(1) or {}).get("subtitle") or (positions.get(1) or {}).get("presenter") or ""
            if t:
                requests.append({
                    "replaceAllText": {
                        "containsText": {"text": "Click to add title", "matchCase": False},
                        "replaceText": t
                    }
                })
            if s:
                requests.append({
                    "replaceAllText": {
                        "containsText": {"text": "Click to add subtitle", "matchCase": False},
                        "replaceText": s
                    }
                })
        except Exception:
            pass

        if requests:
            self.slides_service.presentations().batchUpdate(presentationId=pres_id, body={"requests": requests}).execute()
        # Build export link (PDF)
        export_link = f"https://docs.google.com/presentation/d/{pres_id}/export/pptx"
        return pres_id, export_link

    def create_programmatic_from_positions(
        self,
        *,
        new_title: str,
        positions: dict[int, dict[str, Any]],  # keys 1..7
        theme_bg: str | None = "#F8F9FA",
    ) -> Tuple[str, str | None]:
        """Programmatically create a 7-slide deck using predefined layouts only.

        - Uses createSlide with layouts that provide placeholders (no custom shapes).
        - Then fills placeholders using `_fill_slide`, preserving default styles.
        - Applies a subtle background colour if provided.
        """
        # 1) Create empty presentation
        presentation = self.slides_service.presentations().create(body={"title": new_title}).execute()
        pres_id = presentation["presentationId"]
        first_slide_id = None
        try:
            first_slide_id = presentation.get("slides", [{}])[0].get("objectId")
        except Exception:
            first_slide_id = None

        # 2) Build slides with predefined layouts that expose placeholders
        # Layout mapping per requested structure
        layouts = [
            "TITLE",                # 1 Title slide (has title + subtitle placeholders)
            "TITLE_AND_BODY",       # 2 Agenda (bullets in BODY)
            "TITLE_AND_BODY",       # 3 Background / Intro
            "TITLE_AND_TWO_COLUMNS",# 4 Key insights (two columns)
            "TITLE_ONLY",           # 5 Comparison (table under title)
            "TITLE_AND_BODY",       # 6 Visual recap (bullets/paragraph)
            "TITLE_AND_BODY",       # 7 Thank you / CTA
        ]

        reqs: list[Dict[str, Any]] = []
        if first_slide_id:
            reqs.append({"deleteObject": {"objectId": first_slide_id}})

        created_ids: list[str] = []
        for i, layout in enumerate(layouts, start=1):
            obj_id = f"slide_{i}"
            created_ids.append(obj_id)
            reqs.append({
                "createSlide": {
                    "objectId": obj_id,
                    "slideLayoutReference": {"predefinedLayout": layout},
                }
            })
            if theme_bg:
                reqs.append({
                    "updatePageProperties": {
                        "objectId": obj_id,
                        "pageProperties": {
                            "pageBackgroundFill": {
                                "solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme_bg)}, "alpha": 1}
                            }
                        },
                        "fields": "pageBackgroundFill.solidFill.color"
                    }
                })

        # Create pages
        if reqs:
            self.slides_service.presentations().batchUpdate(presentationId=pres_id, body={"requests": reqs}).execute()

        # 3) Fetch the created presentation to get placeholder objectIds
        pres = self._get_presentation(pres_id)

        # 4) Fill placeholders slide-by-slide with fallbacks
        fill_reqs: list[Dict[str, Any]] = []
        for pos in range(1, 8):
            data = positions.get(pos) or {}
            title = data.get("title") or f"Slide {pos}"
            subtitle = data.get("subtitle") or ""
            paragraph = data.get("paragraph") or ""
            bullets = data.get("bullets") or []
            left = data.get("left") or {}
            right = data.get("right") or {}
            # Prefer presenter/date in subtitle on slide 1 when subtitle blank
            if pos == 1 and (not subtitle or not str(subtitle).strip()):
                subtitle = data.get("presenter") or data.get("date") or ""

            # Fallbacks
            title = title or ("Thank You" if pos == 7 else ("Agenda" if pos == 2 else (data.get("section") or "")))
            subtitle = subtitle or (data.get("presenter") or data.get("date") or "")
            # Sanitize bullets: drop empties/whitespace
            bullets = [str(x).strip() for x in (bullets or ([] if pos != 2 else ["Overview", "Key Topics", "Q&A"])) if str(x).strip()]
            # Do NOT use "Coming soon" for slide 6; only allow for slide 3 fallback
            paragraph = paragraph or ("Coming soon" if pos == 3 else "")
            if pos == 4 and not left:
                left = {"heading": "Point", "points": ["Item 1", "Item 2"]}
            if pos == 4 and not right:
                right = {"heading": "Detail", "points": ["Desc 1", "Desc 2"]}

            # Use existing fill helper (0-based index)
            fill_reqs += self._fill_slide(
                pres,
                pos - 1,
                title=title,
                subtitle=subtitle,
                bullets=bullets,
                left=left,
                right=right,
                paragraph=paragraph,
            )

            # Slide-specific tweaks
            if pos == 2:
                # Increase line spacing on agenda body/subtitle
                slide = pres["slides"][1]
                ph2 = self._collect_placeholders(slide)
                target = ph2["BODY"][0] if ph2["BODY"] else (ph2["SUBTITLE"][0] if ph2["SUBTITLE"] else None)
                if target:
                    fill_reqs.append({
                        "updateParagraphStyle": {
                            "objectId": target,
                            "textRange": {"type": "ALL"},
                            "style": {"lineSpacing": 120},
                            "fields": "lineSpacing"
                        }
                    })
                # Ensure bullets look like bullets
                if bullets and target:
                    fill_reqs.append({
                        "createParagraphBullets": {
                            "objectId": target,
                            "textRange": {"type": "ALL"},
                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                        }
                    })
                # Consistent agenda font size and spacing
                if target:
                    fill_reqs.append({
                        "updateTextStyle": {
                            "objectId": target,
                            "textRange": {"type": "ALL"},
                            "style": {"fontSize": {"magnitude": 18, "unit": "PT"}},
                            "fields": "fontSize"
                        }
                    })
                    # Add a little paragraph space to avoid cramped bullets
                    fill_reqs.append({
                        "updateParagraphStyle": {
                            "objectId": target,
                            "textRange": {"type": "ALL"},
                            "style": {"spaceBelow": {"magnitude": 6, "unit": "PT"}},
                            "fields": "spaceBelow"
                        }
                    })

            # Slide 3: ensure 1–2 paragraphs + 2–4 bullets
            if pos == 3:
                # Synthesize bullets if too few
                agenda_src = (positions.get(2, {}) or {}).get("bullets") or []
                if not isinstance(bullets, list):
                    bullets = []
                if len(bullets) < 2:
                    # Derive from agenda items
                    extra = [str(x) for x in agenda_src][:4]
                    for x in extra:
                        if len(bullets) >= 4:
                            break
                        if x not in bullets:
                            bullets.append(x)
                # Ensure paragraph has at least 1 sentence
                if not paragraph:
                    paragraph = (title + ": " + ("; ".join(bullets[:2]) or "Overview")).strip()
                # Try to emulate 1–2 paragraphs by adding a newline
                if "\n\n" not in paragraph and len(paragraph) > 120:
                    mid = len(paragraph) // 2
                    paragraph = paragraph[:mid].rstrip() + "\n\n" + paragraph[mid:].lstrip()

                # Compose paragraph + bullets into a single BODY placeholder
                slide = pres["slides"][2]
                ph3 = self._collect_placeholders(slide)
                target = ph3["BODY"][0] if ph3["BODY"] else (ph3["SUBTITLE"][0] if ph3["SUBTITLE"] else None)
                if target:
                    composed = (paragraph or "").strip()
                    if composed and bullets:
                        composed += "\n\n" + "\n".join([str(x) for x in bullets[:4]])
                    elif bullets and not composed:
                        composed = "\n".join([str(x) for x in bullets[:4]])
                    # Replace content with composed text
                    fill_reqs += self._clear_and_insert(pres, target, composed)
                    # Apply bullet styling if any bullets present
                    if bullets:
                        fill_reqs.append({
                            "createParagraphBullets": {
                                "objectId": target,
                                "textRange": {"type": "ALL"},
                                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                            }
                        })

            # Slide 5: comparison table (2–3 columns) if points available
            if pos == 5:
                slide5 = pres["slides"][4]
                slide_obj_id = slide5["objectId"]
                # Clear any BODY placeholders to avoid duplicated background content when table is used
                ph5 = self._collect_placeholders(slide5)
                for oid in ph5["BODY"]:
                    # Only clear if there is actual text; placeholders with only hint text have length 0
                    if self._text_len(pres, oid) > 0:
                        fill_reqs.append({"deleteText": {"objectId": oid, "textRange": {"type": "ALL"}}})
                # Remove BODY placeholder shapes entirely so the table is not hidden behind them
                for oid in ph5["BODY"]:
                    fill_reqs.append({"deleteObject": {"objectId": oid}})
                lpts = (left or {}).get("points") or []
                rpts = (right or {}).get("points") or []
                # Optional third column if provided via data["center"] or extra bullets
                center_data = (data.get("center") or {}) if isinstance(data, dict) else {}
                ccol = center_data.get("points") if isinstance(center_data, dict) else None
                # If both lpts/rpts empty, seed with agenda-derived items BEFORE computing rows
                if not lpts and not rpts:
                    agenda = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or []) if str(x).strip()]
                    mid = max(1, len(agenda) // 2) if agenda else 1
                    lpts = agenda[:mid]
                    rpts = agenda[mid:]
                    if not rpts:
                        rpts = [f"Why {t} matters" for t in lpts[:3]]
                # rows = header + max(len(l), len(r), len(c)); ensure at least 3 body rows
                rows = max(1 + max(len(lpts), len(rpts), len(ccol or []), 1), 4)
                table_id = f"comp_table_s{pos}_{uuid.uuid4().hex[:6]}"
                print(f"[SlidesAPI] Slide 5: creating table id={table_id} rows={rows} cols={(3 if ccol else 2)}")
                # Compute page size for precise centering and position below the title
                page_w = ((pres.get("pageSize", {}) or {}).get("width", {}) or {}).get("magnitude", 960)
                # Determine bottom of the title placeholder to position table below it
                title_bottom = 160
                if ph5.get("TITLE"):
                    try:
                        t_id = ph5["TITLE"][0]
                        t_el = next((el for el in (slide5.get("pageElements") or []) if el.get("objectId") == t_id), None)
                        if t_el:
                            th = float(((t_el.get("size") or {}).get("height") or {}).get("magnitude", 0))
                            ty = float(((t_el.get("transform") or {}).get("translateY", 0)))
                            title_bottom = ty + th + 24.0
                    except Exception:
                        pass
                table_w = min(700.0, float(page_w) - 120.0)
                table_x = (float(page_w) - table_w) / 2.0
                table_y = max(180.0, title_bottom)
                # Create centered table below title and within safe bounds
                fill_reqs.append({
                    "createTable": {
                        "objectId": table_id,
                        "elementProperties": {
                            "pageObjectId": slide_obj_id,
                            # Use computed width so it's safely inside margins
                            "size": {"width": {"magnitude": table_w, "unit": "PT"}, "height": {"magnitude": 240, "unit": "PT"}},
                            # Center horizontally and place below the title
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": table_x, "translateY": table_y, "unit": "PT"},
                        },
                        "rows": rows,
                        "columns": 2 if not ccol else 3,
                    }
                })
                # Ensure explicit column widths and row heights so the table renders predictably
                for cidx in range(2 if not ccol else 3):
                    fill_reqs.append({
                        "updateTableColumnProperties": {
                            "objectId": table_id,
                            "columnIndices": [cidx],
                            "tableColumnProperties": {"columnWidth": {"magnitude": 600 / (2 if not ccol else 3), "unit": "PT"}},
                            "fields": "columnWidth"
                        }
                    })
                for ridx in range(rows):
                    fill_reqs.append({
                        "updateTableRowProperties": {
                            "objectId": table_id,
                            "rowIndices": [ridx],
                            "tableRowProperties": {"minRowHeight": {"magnitude": 32, "unit": "PT"}},
                            "fields": "minRowHeight"
                        }
                    })
                # Apply visible borders for entire table (all cell outlines)
                cols = 2 if not ccol else 3
                border_color = _hex_to_rgb_dict("#D1D5DB")  # Tailwind gray-300
                fill_reqs.append({
                    "updateTableBorderProperties": {
                        "objectId": table_id,
                        "tableRange": {"location": {"rowIndex": 0, "columnIndex": 0}, "rowSpan": rows, "columnSpan": cols},
                        "tableBorderProperties": {
                            "dashStyle": "SOLID",
                            "weight": {"magnitude": 1, "unit": "PT"},
                            "tableBorderFill": {"solidFill": {"color": {"rgbColor": border_color}}}
                        },
                        "borderPosition": "ALL",
                        "fields": "dashStyle,weight,tableBorderFill.solidFill.color"
                    }
                })
                # Headers (derive meaningful titles from headings or agenda)
                hdr_l = (left or {}).get("heading")
                hdr_r = (right or {}).get("heading")
                if not (hdr_l and hdr_r):
                    agenda_terms = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or [])]
                    hdr_l = hdr_l or (agenda_terms[0] if len(agenda_terms) > 0 else "Option A")
                    hdr_r = hdr_r or (agenda_terms[1] if len(agenda_terms) > 1 else "Option B")
                headers = [hdr_l, hdr_r]
                if ccol:
                    hdr_c = center_data.get("heading")
                    if not hdr_c:
                        agenda_terms = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or [])]
                        hdr_c = agenda_terms[2] if len(agenda_terms) > 2 else "Option C"
                    headers.append(hdr_c)
                for c, text in enumerate(headers):
                    fill_reqs.append({
                        "insertText": {
                            "objectId": table_id,
                            "cellLocation": {"rowIndex": 0, "columnIndex": c},
                            "text": str(text),
                        }
                    })
                    # Bold header row cells
                    fill_reqs.append({
                        "updateTextStyle": {
                            "objectId": table_id,
                            "cellLocation": {"rowIndex": 0, "columnIndex": c},
                            "textRange": {"type": "ALL"},
                            "style": {"bold": True},
                            "fields": "bold"
                        }
                    })
                    # Header background for better contrast
                    fill_reqs.append({
                        "updateTableCellProperties": {
                            "objectId": table_id,
                            "tableRange": {"location": {"rowIndex": 0, "columnIndex": c}, "rowSpan": 1, "columnSpan": 1},
                            "tableCellProperties": {"tableCellBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#EEF4FF")}}}},
                            "fields": "tableCellBackgroundFill.solidFill.color"
                        }
                    })
                # Thicker bottom border under header row for separation
                fill_reqs.append({
                    "updateTableBorderProperties": {
                        "objectId": table_id,
                        "tableRange": {"location": {"rowIndex": 0, "columnIndex": 0}, "rowSpan": 1, "columnSpan": cols},
                        "tableBorderProperties": {
                            "dashStyle": "SOLID",
                            "weight": {"magnitude": 2, "unit": "PT"},
                            "tableBorderFill": {"solidFill": {"color": {"rgbColor": border_color}}}
                        },
                        "borderPosition": "BOTTOM",
                        "fields": "dashStyle,weight,tableBorderFill.solidFill.color"
                    }
                })
                # Rows content
                for r in range(rows - 1):
                    if r < len(lpts):
                        fill_reqs.append({
                            "insertText": {"objectId": table_id, "cellLocation": {"rowIndex": r + 1, "columnIndex": 0}, "text": str(lpts[r])}
                        })
                    if r < len(rpts):
                        fill_reqs.append({
                            "insertText": {"objectId": table_id, "cellLocation": {"rowIndex": r + 1, "columnIndex": 1}, "text": str(rpts[r])}
                        })
                    if ccol and r < len(ccol or []):
                        fill_reqs.append({
                            "insertText": {"objectId": table_id, "cellLocation": {"rowIndex": r + 1, "columnIndex": 2}, "text": str((ccol or [])[r])}
                        })
                    # Alternating row background for readability
                    if (r % 2) == 1:
                        fill_reqs.append({
                            "updateTableCellProperties": {
                                "objectId": table_id,
                                "tableRange": {"location": {"rowIndex": r + 1, "columnIndex": 0}, "rowSpan": 1, "columnSpan": 2 if not ccol else 3},
                                "tableCellProperties": {"tableCellBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#FAFBFC")}}}},
                                "fields": "tableCellBackgroundFill.solidFill.color"
                            }
                        })

                # If both lpts/rpts empty, seed with agenda-derived items (last resort)
                if not lpts and not rpts:
                    agenda = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or [])]
                    seed = agenda[:4] or ["Pros", "Cons", "Cost", "Performance"]
                    for idx, item in enumerate(seed[:rows - 1]):
                        fill_reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": idx + 1, "columnIndex": 0}, "text": item}})
                        fill_reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": idx + 1, "columnIndex": 1}, "text": f"Insight on {item}"}})

            # Slide 6: infographic-style icon blocks using ellipses with short texts
            if pos == 6:
                slide_obj_id = pres["slides"][5]["objectId"]
                labels = [str(x) for x in (bullets or []) if str(x).strip()]
                # Robust fallback to guarantee 4 labels
                if len(labels) < 4:
                    ag = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or []) if str(x).strip()]
                    labels.extend(ag)
                if len(labels) < 4:
                    defaults = [
                        "AI Advancements",
                        "Ethical Considerations",
                        "AI in Healthcare",
                        "Future Trends",
                        "Top Industries"
                    ]
                    for d in defaults:
                        if len(labels) >= 4:
                            break
                        labels.append(d)
                icons = labels[:4]
                # Even horizontal spacing within safe bounds
                # Keep circles within safe bounds: compute dynamic spacing for 4 items
                page_w = ((pres.get("pageSize", {}) or {}).get("width", {}) or {}).get("magnitude", 960)
                side_margin = 90
                n = len(icons)
                y = 250
                circle_size = 110
                usable = max(0, page_w - 2 * side_margin)
                gap = 0 if n <= 1 else (usable - n * circle_size) / (n - 1)
                base_x = side_margin
                for i, txt in enumerate(icons):
                    oid = f"icon_s{pos}_{i+1}"
                    fill_reqs.append({
                        "createShape": {
                            "objectId": oid,
                            "shapeType": "ELLIPSE",
                            "elementProperties": {
                                "pageObjectId": slide_obj_id,
                                "size": {"width": {"magnitude": circle_size, "unit": "PT"}, "height": {"magnitude": circle_size, "unit": "PT"}},
                                "transform": {"scaleX": 1, "scaleY": 1, "translateX": base_x + gap * i, "translateY": y, "unit": "PT"},
                            },
                        }
                    })
                    # Soft fill color variations
                    rgb = _hex_to_rgb_dict("#A0C4FF" if i % 2 == 0 else "#BDE0FE")
                    fill_reqs.append({
                        "updateShapeProperties": {
                            "objectId": oid,
                            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": rgb}}}},
                            "fields": "shapeBackgroundFill.solidFill.color"
                        }
                    })
                    # Insert step number inside circle
                    fill_reqs.append({"insertText": {"objectId": oid, "insertionIndex": 0, "text": str(i+1)}})
                    fill_reqs.append({
                        "updateTextStyle": {
                            "objectId": oid,
                            "textRange": {"type": "ALL"},
                            "style": {"bold": True, "fontSize": {"magnitude": 18, "unit": "PT"}},
                            "fields": "bold,fontSize"
                        }
                    })
                    # Label under each circle
                    lab_id = f"icon_label_s{pos}_{i+1}"
                    fill_reqs.append({
                        "createShape": {
                            "objectId": lab_id,
                            "shapeType": "TEXT_BOX",
                            "elementProperties": {
                                "pageObjectId": slide_obj_id,
                                "size": {"width": {"magnitude": circle_size + 20, "unit": "PT"}, "height": {"magnitude": 28, "unit": "PT"}},
                                "transform": {"scaleX": 1, "scaleY": 1, "translateX": base_x + gap * i - 10, "translateY": y + circle_size + 8, "unit": "PT"}
                            }
                        }
                    })
                    fill_reqs.append({"insertText": {"objectId": lab_id, "text": str(txt)}})
                    fill_reqs.append(_text_style_req(lab_id, font=DEFAULT_THEME.get("font", "Inter"), size_pt=14, bold=False, color_hex=DEFAULT_THEME.get("text", "#212529")))
                    fill_reqs.append({
                        "updateParagraphStyle": {
                            "objectId": lab_id,
                            "textRange": {"type": "ALL"},
                            "style": {"alignment": "CENTER"},
                            "fields": "alignment"
                        }
                    })
                y = 250
                circle_size = 110
                usable = max(0, page_w - 2 * side_margin)
                gap = 0 if n <= 1 else (usable - n * circle_size) / (n - 1)
                base_x = side_margin
                for i, txt in enumerate(icons):
                    oid = f"icon_{i+1}"
                    fill_reqs.append({
                        "createShape": {
                            "objectId": oid,
                            "shapeType": "ELLIPSE",
                            "elementProperties": {
                                "pageObjectId": slide_obj_id,
                                "size": {"width": {"magnitude": circle_size, "unit": "PT"}, "height": {"magnitude": circle_size, "unit": "PT"}},
                                "transform": {"scaleX": 1, "scaleY": 1, "translateX": base_x + gap * i, "translateY": y, "unit": "PT"},
                            },
                        }
                    })
                    # Soft fill color variations
                    rgb = _hex_to_rgb_dict("#A0C4FF" if i % 2 == 0 else "#BDE0FE")
                    fill_reqs.append({
                        "updateShapeProperties": {
                            "objectId": oid,
                            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": rgb}}}},
                            "fields": "shapeBackgroundFill.solidFill.color"
                        }
                    })
                    # Insert step number inside circle
                    fill_reqs.append({"insertText": {"objectId": oid, "insertionIndex": 0, "text": str(i+1)}})
                    fill_reqs.append({
                        "updateTextStyle": {
                            "objectId": oid,
                            "textRange": {"type": "ALL"},
                            "style": {"bold": True, "fontSize": {"magnitude": 18, "unit": "PT"}},
                            "fields": "bold,fontSize"
                        }
                    })
                    # Label under each circle
                    lab_id = f"icon_label_{i+1}"
                    fill_reqs.append({
                        "createShape": {
                            "objectId": lab_id,
                            "shapeType": "TEXT_BOX",
                            "elementProperties": {
                                "pageObjectId": slide_obj_id,
                                "size": {"width": {"magnitude": circle_size + 20, "unit": "PT"}, "height": {"magnitude": 28, "unit": "PT"}},
                                "transform": {"scaleX": 1, "scaleY": 1, "translateX": base_x + gap * i - 10, "translateY": y + circle_size + 8, "unit": "PT"}
                            }
                        }
                    })
                    fill_reqs.append({"insertText": {"objectId": lab_id, "text": str(txt)}})
                    fill_reqs.append(_text_style_req(lab_id, font=DEFAULT_THEME.get("font", "Inter"), size_pt=14, bold=False, color_hex=DEFAULT_THEME.get("text", "#212529")))
                    fill_reqs.append({
                        "updateParagraphStyle": {
                            "objectId": lab_id,
                            "textRange": {"type": "ALL"},
                            "style": {"alignment": "CENTER"},
                            "fields": "alignment"
                        }
                    })

            # Slide 4: two-column fallback + visual distinction
            if pos == 4:
                slide = pres["slides"][3]
                ph4 = self._collect_placeholders(slide)
                left_id = ph4["BODY"][0] if ph4["BODY"] else None
                right_id = ph4["BODY"][1] if len(ph4["BODY"]) > 1 else None
                lpts = (left or {}).get("points") or []
                rpts = (right or {}).get("points") or []
                if (not lpts and not rpts) and (left_id or right_id):
                    agenda = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or [])]
                    mid = max(1, len(agenda) // 2)
                    lpts = agenda[:mid]
                    rpts = agenda[mid:]
                    if not rpts:  # ensure both sides have something
                        rpts = [f"Why {t} matters" for t in lpts[:3]]
                # Ensure at least 4 bullets per column
                if len(lpts) < 4 or len(rpts) < 4:
                    agenda = [str(x) for x in ((positions.get(2, {}) or {}).get("bullets") or [])]
                    while len(lpts) < 4 and agenda:
                        lpts.append(agenda.pop(0))
                    while len(rpts) < 4 and agenda:
                        rpts.append(agenda.pop(0) if agenda else "Detail")
                # Apply soft background fills to differentiate columns
                if left_id:
                    fill_reqs.append({
                        "updateShapeProperties": {
                            "objectId": left_id,
                            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#F1F7FF")}}}},
                            "fields": "shapeBackgroundFill.solidFill.color"
                        }
                    })
                if right_id:
                    fill_reqs.append({
                        "updateShapeProperties": {
                            "objectId": right_id,
                            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#F7F9FC")}}}},
                            "fields": "shapeBackgroundFill.solidFill.color"
                        }
                    })
                # Ensure bullets are applied in both columns
                for tgt in [left_id, right_id]:
                    if tgt:
                        fill_reqs.append({
                            "createParagraphBullets": {
                                "objectId": tgt,
                                "textRange": {"type": "ALL"},
                                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                            }
                        })

            # Slide 7: Thank You bold + centered
            if pos == 7:
                slide = pres["slides"][6]
                ph7 = self._collect_placeholders(slide)
                tgt = ph7["TITLE"][0] if ph7["TITLE"] else (ph7["BODY"][0] if ph7["BODY"] else None)
                if tgt:
                    fill_reqs.append({
                        "updateTextStyle": {
                            "objectId": tgt,
                            "textRange": {"type": "ALL"},
                            "style": {"bold": True},
                            "fields": "bold"
                        }
                    })
                    fill_reqs.append({
                        "updateParagraphStyle": {
                            "objectId": tgt,
                            "textRange": {"type": "ALL"},
                            "style": {"alignment": "CENTER"},
                            "fields": "alignment"
                        }
                    })

        if fill_reqs:
            self.slides_service.presentations().batchUpdate(presentationId=pres_id, body={"requests": fill_reqs}).execute()

        export_link = f"https://docs.google.com/presentation/d/{pres_id}/export/pptx"
        return pres_id, export_link

    # Convenience: download the pptx locally (optional)
    def download_pptx(self, export_link: str, *, out_path: str) -> None:
        request = self.drive_service._http.request  # pylint: disable=protected-access
        # http.request signature: request(uri, method="GET", ...)
        _, response = request(export_link, "GET")
        with open(out_path, "wb") as fp:
            fp.write(response)

# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_batch_update(
    slides_content: List[Dict[str, str]],
    *,
    theme_colors: Dict[str, str] | None = None,
    include_timeline: bool = True,
) -> List[Dict[str, Any]]:

    """Return a list of Google Slides API ``requests`` objects.

    *slides_content* should already be ordered according to ``SLIDE_KINDS_ORDER``
    (with the Timeline slide omitted if *include_timeline* is ``False``).

    NOTE: This only builds *structural* requests – styling rules are greatly
    simplified for brevity. Adjust as needed.
    """

    theme = {**DEFAULT_THEME, **(theme_colors or {})}

    requests: List[Dict[str, Any]] = []

    def _create_slide(layout: str = "BLANK") -> str:
        slide_id = f"slide_{len(requests)}"
        requests.append({
            "createSlide": {
                "objectId": slide_id,
                "slideLayoutReference": {"predefinedLayout": layout},
            }
        })
        # Apply background colour if theme provided
        if theme.get("background"):
            requests.append({
                "updatePageProperties": {
                    "objectId": slide_id,
                    "pageProperties": {
                        "pageBackgroundFill": {
                            "solidFill": {
                                "color": {"rgbColor": _hex_to_rgb_dict(theme["background"]) },
                                "alpha": 1,
                            }
                        }
                    },
                    "fields": "pageBackgroundFill.solidFill.color"
                }
            })
        return slide_id

    # Defensive: normalize slides while preserving optional 'type' and 'columns'
    # Structure: {title: str, type: str|None, bullets: List[str], columns: dict|None}
    normalized: List[Dict[str, Any]] = []
    for item in (slides_content or []):
        if not item:
            continue
        title = str(item.get("title") or "Slide")
        content = item.get("content")
        slide_type = item.get("type")
        columns = item.get("columns")
        bullets: List[str]
        if isinstance(content, list):
            bullets = [str(x) for x in content if x is not None]
        elif isinstance(content, str):
            bullets = [s for s in content.split("\n") if s is not None]
        else:
            bullets = []

        # Map strict schema content into renderer-friendly fields
        if isinstance(content, dict):
            # Table schema: content.table.left/right {heading, points[]}
            tbl = content.get("table")
            if isinstance(tbl, dict):
                columns = {
                    "left": {
                        "heading": str((tbl.get("left") or {}).get("heading") or "Left"),
                        "points": [str(p) for p in ((tbl.get("left") or {}).get("points") or [])],
                    },
                    "right": {
                        "heading": str((tbl.get("right") or {}).get("heading") or "Right"),
                        "points": [str(p) for p in ((tbl.get("right") or {}).get("points") or [])],
                    },
                }
                slide_type = slide_type or "table"

            # Double column schema: left_points/right_points plus optional headings
            lp = content.get("left_points")
            rp = content.get("right_points")
            if isinstance(lp, list) or isinstance(rp, list):
                columns = {
                    "left": {
                        "heading": str(content.get("left_heading") or "Left"),
                        "points": [str(p) for p in (lp or [])],
                    },
                    "right": {
                        "heading": str(content.get("right_heading") or "Right"),
                        "points": [str(p) for p in (rp or [])],
                    },
                }
                slide_type = slide_type or "double_column"

        normalized.append({
            "title": title,
            "type": slide_type,
            "bullets": bullets,
            "columns": columns,
            "raw": item,
        })

    if not normalized:
        normalized = [{"title": "Untitled", "bullets": []}]

    # First slide fields
    first_title = normalized[0]["title"]
    first_subtitle = "\n".join(normalized[0]["bullets"]) if normalized[0]["bullets"] else ""
    presenter = ""
    try:
        presenter = str((normalized[0].get("raw") or {}).get("presenter") or "")
    except Exception:
        presenter = ""

    # 1. Title slide (custom shapes on BLANK layout)
    title_slide_id = _create_slide()
    # Guard against helper ever returning None
    title_reqs = _insert_title_and_subtitle(title_slide_id, first_title, first_subtitle, theme, presenter)
    if title_reqs is None:
        # Robust fallback: create a centered title and subtitle
        print("Warning: _insert_title_and_subtitle returned None; using fallback title rendering.")
        fg = theme.get("primary", "#FFFFFF")
        font = theme.get("font", "Inter")
        title_reqs = [
            {
                "createShape": {
                    "objectId": f"{title_slide_id}_title",
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": title_slide_id,
                        "size": {"height": {"magnitude": 80, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 160, "unit": "PT"}
                    }
                }
            },
            {"insertText": {"objectId": f"{title_slide_id}_title", "text": first_title or "Title"}},
            _text_style_req(f"{title_slide_id}_title", font=font, size_pt=theme.get("title_size", 42), bold=True, color_hex=fg),
            {
                "createShape": {
                    "objectId": f"{title_slide_id}_subtitle",
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": title_slide_id,
                        "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 560, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 80, "translateY": 240, "unit": "PT"}
                    }
                }
            },
            {"insertText": {"objectId": f"{title_slide_id}_subtitle", "text": first_subtitle or ""}},
            _text_style_req(f"{title_slide_id}_subtitle", font=font, size_pt=theme.get("subtitle_size", 24), bold=False, color_hex=fg),
        ]
    requests.extend(title_reqs)

    # Build remaining slides by type (skip 'overview' slides per requirements)
    for slide in [s for s in normalized[1:] if (str(s.get("type") or "").lower() != "overview")]:
        slide_id = _create_slide()
        title = slide.get("title") or "Slide"
        s_type = (slide.get("type") or "").lower()
        # Timeline handling: accept aliases and render events if present, else fallback to bullets
        if s_type in ("timeline", "background", "timeline/background", "timeline_background"):
            raw = slide.get("raw") or {}
            content = raw.get("content") or {}
            events = content.get("timeline") or content.get("events") or []
            if isinstance(events, list) and len(events) >= 3:
                reqs = _insert_timeline(slide_id, title or "Timeline", events, theme)
                requests.extend(reqs)
                vis = (slide.get("visual_suggestion") or "").strip()
                if vis:
                    requests.extend(_insert_visual_hint(slide_id, vis, theme))
                continue
            # Fallback: bullet timeline
            lines = slide.get("bullets") or []
            reqs = _insert_bullets(slide_id, title or "Timeline", lines, theme)
            requests.extend(reqs or [])
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue
        if s_type in ("double_col", "double_column") and isinstance(slide.get("columns"), dict):
            cols = slide["columns"]
            left = cols.get("left") or {}
            right = cols.get("right") or {}
            reqs = _insert_two_columns(
                slide_id,
                title,
                left.get("heading") or "Left",
                left.get("points") or [],
                right.get("heading") or "Right",
                right.get("points") or [],
                theme,
            )
            requests.extend(reqs)
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue

        if s_type == "infographic":
            # Render large highlighted stats/points panels from bullets
            bullets = slide.get("bullets") or []
            reqs = _insert_infographic(slide_id, title, bullets, theme)
            requests.extend(reqs)
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue

        if s_type == "process_flow":
            # Render a left-to-right node flow from bullets (fallback if <4)
            bullets = slide.get("bullets") or []
            reqs = _insert_process_flow(slide_id, title, bullets, theme)
            requests.extend(reqs)
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue

        # Comparison / Table slide handling
        if s_type in ("comparison", "table", "tabular", "tabular_comparison"):
            cols_dict = slide.get("columns")
            if isinstance(cols_dict, dict):
                left = cols_dict.get("left") or {}
                right = cols_dict.get("right") or {}
                reqs = _insert_table(
                    slide_id,
                    title,
                    left.get("heading") or "Left",
                    right.get("heading") or "Right",
                    left.get("points") or [],
                    right.get("points") or [],
                    theme,
                )
                requests.extend(reqs)
                vis = (slide.get("visual_suggestion") or "").strip()
                if vis:
                    requests.extend(_insert_visual_hint(slide_id, vis, theme))
                continue

        if s_type == "cta":
            # Big headline + short supporting bullets and a call-to-action button
            bullets = slide.get("bullets") or []
            reqs = _insert_cta(slide_id, title, bullets, theme)
            requests.extend(reqs)
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue

        # Key takeaways: treat like a bullet slide with 3–4 points
        if s_type in ("key_takeaways", "takeaways"):
            bullets = slide.get("bullets") or []
            reqs = _insert_bullets(slide_id, title or "Key Takeaways", bullets, theme)
            requests.extend(reqs or [])
            vis = (slide.get("visual_suggestion") or "").strip()
            if vis:
                requests.extend(_insert_visual_hint(slide_id, vis, theme))
            continue

        if s_type == "table":
            # Preferred: explicit columns dict
            cols = slide.get("columns")
            if isinstance(cols, dict):
                left = cols.get("left") or {}
                right = cols.get("right") or {}
                reqs = _insert_table(
                    slide_id,
                    title,
                    left.get("heading") or "Left",
                    right.get("heading") or "Right",
                    left.get("points") or [],
                    right.get("points") or [],
                    theme,
                )
            else:
                # Fallback: parse from pipe text or use simple text grid
                raw = slide.get("raw") or {}
                content = raw.get("content") or ""
                if "|" in str(content):
                    table_text = str(content)
                    lines = [ln.strip() for ln in table_text.split("\n") if ln.strip()]
                    left_points = [ln.split("|")[0].strip() for ln in lines[1:]] if len(lines) > 1 else []
                    right_points = [ln.split("|")[-1].strip() for ln in lines[1:]] if len(lines) > 1 else []
                    reqs = _insert_table(slide_id, title, "Left", "Right", left_points, right_points, theme)
                else:
                    table_text = "\n".join(slide.get("bullets") or []) or content
                    reqs = _insert_table_text(slide_id, title, str(table_text), theme)
            requests.extend(reqs)
            continue

        # default bullets or infographic summaries
        bullets = slide.get("bullets") or []
        bullet_reqs = _insert_bullets(slide_id, title, bullets, theme)
        if bullet_reqs is None:
            print(f"Warning: _insert_bullets returned None for slide '{title}'.")
            bullet_reqs = []
        requests.extend(bullet_reqs)


    return requests


def _insert_timeline(slide_id: str, title: str, events: List[Dict[str, str]], theme: Dict[str, str]) -> List[Dict[str, Any]]:
    """Render a horizontal timeline line with event nodes and labels.

    events: list of {label, description}
    """
    reqs: List[Dict[str, Any]] = []
    # Title
    reqs.append({
        "createShape": {
            "objectId": f"{slide_id}_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 30, "unit": "PT"}
            }
        }
    })
    reqs.append({"insertText": {"objectId": f"{slide_id}_title", "text": title}})
    reqs.append(_text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=28, bold=True, color_hex=theme.get("primary", "#0077B6")))

    # Baseline
    y = 220
    reqs.append({
        "createLine": {
            "objectId": f"{slide_id}_baseline",
            "lineCategory": "STRAIGHT",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"height": {"magnitude": 0, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": y, "unit": "PT"}
            }
        }
    })
    reqs.append({
        "updateLineProperties": {
            "objectId": f"{slide_id}_baseline",
            "lineProperties": {"lineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("secondary", "#48CAE4"))}}}},
            "fields": "lineFill.solidFill.color"
        }
    })

    # Nodes
    n = max(3, min(6, len(events)))
    step = 640 / (n + 1)
    for i in range(n):
        ev = events[i] if i < len(events) else {}
        label = str(ev.get("label") or ev.get("title") or "")
        desc = str(ev.get("description") or ev.get("caption") or "")
        x = 40 + (i + 1) * step
        # Vertical tick
        reqs.append({
            "createLine": {
                "objectId": f"{slide_id}_tick_{i}",
                "lineCategory": "STRAIGHT",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 80, "unit": "PT"}, "width": {"magnitude": 0, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y-40, "unit": "PT"}
                }
            }
        })
        reqs.append({
            "updateLineProperties": {
                "objectId": f"{slide_id}_tick_{i}",
                "lineProperties": {"lineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("accent", "#90E0EF"))}}}},
                "fields": "lineFill.solidFill.color"
            }
        })
        # Node circle (as shape)
        reqs.append({
            "createShape": {
                "objectId": f"{slide_id}_node_{i}",
                "shapeType": "ELLIPSE",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 16, "unit": "PT"}, "width": {"magnitude": 16, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": x-8, "translateY": y-8, "unit": "PT"}
                }
            }
        })
        reqs.append({
            "updateShapeProperties": {
                "objectId": f"{slide_id}_node_{i}",
                "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("primary", "#0077B6"))}, "alpha": 1}}},
                "fields": "shapeBackgroundFill.solidFill.color"
            }
        })
        # Label text box below
        reqs.append({
            "createShape": {
                "objectId": f"{slide_id}_label_{i}",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 160, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": x-80, "translateY": y+10, "unit": "PT"}
                }
            }
        })
        text = label + ("\n" + desc if desc else "")
        reqs.append({"insertText": {"objectId": f"{slide_id}_label_{i}", "text": text}})
        reqs.append(_text_style_req(f"{slide_id}_label_{i}", font=theme.get("font", "Inter"), size_pt=14, bold=False, color_hex=theme.get("text", "#212529")))

    return reqs


def _insert_infographic(slide_id: str, title: str, bullets: List[str], theme: Dict[str, str]) -> List[Dict[str, Any]]:
    """Infographic slide: headline + 2–6 large highlight panels from bullets.

    - Ensures WCAG-like contrast by using white box fills on dark backgrounds.
    - Falls back gracefully if bullets are missing.
    """
    reqs: List[Dict[str, Any]] = []
    # Title
    reqs.append({
        "createShape": {
            "objectId": f"{slide_id}_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 40, "unit": "PT"}
            }
        }
    })
    reqs.append({"insertText": {"objectId": f"{slide_id}_title", "text": title}})
    reqs.append(_text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("accent", "#ff6f3c")))

    # Panels (up to 6) laid out in two rows
    items = [b for b in bullets if isinstance(b, str) and b.strip()]
    if not items:
        items = ["Key metric one", "Key metric two", "Key metric three"]
    items = items[:6]

    cols = 3 if len(items) >= 4 else max(1, min(2, len(items)))
    rows = (len(items) + cols - 1) // cols

    # Fit panels within a safe canvas (left/right margins of 40pt)
    total_width = 640
    margin = 40
    usable_width = total_width - 2 * margin
    gap = 16
    panel_width = (usable_width - gap * (cols - 1)) / max(1, cols)
    panel_height = 110
    start_x = margin
    start_y = 120

    body_text = theme.get("text", "#212529")

    for i, text in enumerate(items):
        r = i // cols
        c = i % cols
        x = start_x + c * (panel_width + gap)
        y = start_y + r * (panel_height + gap)
        if y + panel_height > 540:
            y = 120
            x = start_x + (c + cols) * (panel_width + gap)
        obj_id = f"{slide_id}_panel_{i}"
        reqs.extend([
            {
                "createShape": {
                    "objectId": obj_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {"height": {"magnitude": panel_height, "unit": "PT"}, "width": {"magnitude": panel_width, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}
                    }
                }
            },
            {"insertText": {"objectId": obj_id, "text": text}},
            _text_style_req(obj_id, font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 18), bold=True, color_hex=body_text),
            {
                "updateShapeProperties": {
                    "objectId": obj_id,
                    "shapeProperties": {
                        "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")}, "alpha": 1}},
                        "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("accent", "#ff6f3c"))}}}, "weight": {"magnitude": 1, "unit": "PT"}}
                    },
                    "fields": "shapeBackgroundFill.solidFill.color,outline.outlineFill.solidFill.color,outline.weight"
                }
            },
        ])
    return reqs


def _insert_process_flow(slide_id: str, title: str, steps: List[str], theme: Dict[str, str]) -> List[Dict[str, Any]]:
    """Process flow: title + horizontal nodes connected by arrows.

    - Uses bullets as steps. If <4 steps, still renders nodes; renderer is robust.
    - White node fill on dark backgrounds for readability.
    """
    reqs: List[Dict[str, Any]] = []
    # Title
    reqs.append({
        "createShape": {
            "objectId": f"{slide_id}_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 40, "unit": "PT"}
            }
        }
    })
    reqs.append({"insertText": {"objectId": f"{slide_id}_title", "text": title}})
    reqs.append(_text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("accent", "#ff6f3c")))

    items = [s for s in steps if isinstance(s, str) and s.strip()]
    if not items:
        items = ["Step 1", "Step 2", "Step 3", "Step 4"]
    items = items[:7]

    node_w, node_h = 160, 70
    gap = 30
    total_w = len(items) * node_w + (len(items) - 1) * gap
    start_x = max(40, 360 - total_w / 2)
    y = 180
    text_color = "#222222" if _is_dark(theme.get("background", "#1e1e1e")) else theme.get("secondary", "#333333")

    node_ids: List[str] = []
    for i, label in enumerate(items):
        x = start_x + i * (node_w + gap)
        node_id = f"{slide_id}_node_{i}"
        node_ids.append(node_id)
        reqs.extend([
            {
                "createShape": {
                    "objectId": node_id,
                    "shapeType": "ROUNDED_RECTANGLE",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {"height": {"magnitude": node_h, "unit": "PT"}, "width": {"magnitude": node_w, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}
                    }
                }
            },
            {
                "updateShapeProperties": {
                    "objectId": node_id,
                    "shapeProperties": {
                        "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")}, "alpha": 1}},
                        "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("accent", "#ff6f3c"))}}}, "weight": {"magnitude": 1.5, "unit": "PT"}}
                    },
                    "fields": "shapeBackgroundFill.solidFill.color,outline.outlineFill.solidFill.color,outline.weight"
                }
            },
            {"insertText": {"objectId": node_id, "text": label}},
            _text_style_req(node_id, font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 18), bold=False, color_hex=text_color),
        ])

    # Connect nodes with arrows
    for i in range(len(node_ids) - 1):
        reqs.append({
            "createLine": {
                "objectId": f"{slide_id}_arrow_{i}",
                "lineCategory": "STRAIGHT",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 0, "unit": "PT"}, "width": {"magnitude": gap, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": start_x + (i + 1) * node_w + i * gap, "translateY": y + node_h / 2, "unit": "PT"}
                }
            }
        })
        # Style line color
        reqs.append({
            "updateLineProperties": {
                "objectId": f"{slide_id}_arrow_{i}",
                "lineProperties": {
                    "lineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("primary", "#FFFFFF"))}}}
                },
                "fields": "lineFill.solidFill.color"
            }
        })
    return reqs


def _insert_cta(slide_id: str, title: str, bullets: List[str], theme: Dict[str, str]) -> List[Dict[str, Any]]:
    """CTA slide: bold headline, brief bullets, and a prominent button-like shape."""
    reqs: List[Dict[str, Any]] = []
    # Headline
    reqs.append({
        "createShape": {
            "objectId": f"{slide_id}_headline",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"height": {"magnitude": 80, "unit": "PT"}, "width": {"magnitude": 640, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 60, "unit": "PT"}
            }
        }
    })
    reqs.append({"insertText": {"objectId": f"{slide_id}_headline", "text": title}})
    reqs.append(_text_style_req(f"{slide_id}_headline", font=theme.get("font", "Inter"), size_pt=max(30, theme.get("subtitle_size", 28)+4), bold=True, color_hex=theme.get("primary", "#FFFFFF")))

    # Supporting bullets
    support = [b for b in bullets if isinstance(b, str) and b.strip()][:6]
    if support:
        reqs.extend(_insert_bullets(slide_id, "", support, theme))

    # Button
    btn_id = f"{slide_id}_cta_btn"
    btn_w, btn_h = 240, 52
    btn_x, btn_y = 200, 360
    reqs.extend([
        {
            "createShape": {
                "objectId": btn_id,
                "shapeType": "ROUNDED_RECTANGLE",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": btn_h, "unit": "PT"}, "width": {"magnitude": btn_w, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": btn_x, "translateY": btn_y, "unit": "PT"}
                }
            }
        },
        {
            "updateShapeProperties": {
                "objectId": btn_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("accent", "#ff6f3c"))}, "alpha": 1}},
                    "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(theme.get("accent", "#ff6f3c"))}}}, "weight": {"magnitude": 0, "unit": "PT"}}
                },
                "fields": "shapeBackgroundFill.solidFill.color,outline.outlineFill.solidFill.color,outline.weight"
            }
        },
        {"insertText": {"objectId": btn_id, "text": "Get Started"}},
        _text_style_req(btn_id, font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 18), bold=True, color_hex="#FFFFFF"),
    ])
    return reqs


# ---------------------------------------------------------------------------
# Internal helpers (text / shapes)
# ---------------------------------------------------------------------------

def _insert_title_and_subtitle(slide_id: str, title: str, subtitle: str, theme: Dict[str, str], presenter: str | None = None) -> List[Dict[str, Any]]:
    reqs: List[Dict[str, Any]] = [
        {
            "createShape": {
                "objectId": f"{slide_id}_title",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 80, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 50, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_title", "text": title or "Title"}},
        _text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("title_size", 40), bold=True, color_hex=theme.get("primary", "#0077B6")),
        {
            "createShape": {
                "objectId": f"{slide_id}_subtitle",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 150, "unit": "PT"}
                }
            }
        },
        {
            "insertText": {
                "objectId": f"{slide_id}_subtitle",
                "text": subtitle or "",
            }
        },
    ]
    # Only apply text style to subtitle when there is actual text; Google API
    # rejects updateTextStyle on an empty text range.
    if (subtitle or "").strip():
        reqs.append(_text_style_req(f"{slide_id}_subtitle", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 24), bold=False, color_hex=theme.get("text", "#212529")))

    # Presenter (optional)
    pres = (presenter or "").strip()
    if pres:
        reqs.append({
            "createShape": {
                "objectId": f"{slide_id}_presenter",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 30, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 220, "unit": "PT"}
                }
            }
        })
        reqs.append({"insertText": {"objectId": f"{slide_id}_presenter", "text": pres}})
        reqs.append(_text_style_req(f"{slide_id}_presenter", font=theme.get("font", "Inter"), size_pt=18, bold=False, color_hex=theme.get("text", "#212529")))
    
    return reqs


def _insert_bullets(slide_id: str, title: str, bullets: List[str], theme: Dict[str, str]) -> List[Dict[str, Any]]:
    reqs: List[Dict[str, Any]] = [
        {
            "createShape": {
                "objectId": f"{slide_id}_title",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 40, "unit": "PT"}
                }
            }
        },
        {
            "insertText": {
                "objectId": f"{slide_id}_title",
                "text": title,
            }
        },
        {
            "createShape": {
                "objectId": f"{slide_id}_body",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 300, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 120, "unit": "PT"}
                }
            }
        }
    ]
    # Clean bullets: trim, de-duplicate (preserve order), and cap to 8
    seen = set()
    cleaned: List[str] = []
    for b in (bullets or []):
        if not isinstance(b, str):
            continue
        s = b.strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        cleaned.append(s)
        if len(cleaned) >= 8:
            break
    bullet_text = "\n".join(cleaned)
    if bullet_text:
        reqs.append(
            {
                "insertText": {
                    "objectId": f"{slide_id}_body",
                    "text": bullet_text,
                }
            }
        )
        # Apply bullet styling only when there is text
        reqs.append(
            {
                "createParagraphBullets": {
                    "objectId": f"{slide_id}_body",
                    "textRange": {"type": "ALL"},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            }
        )
    # Apply styles and contrast-aware fills
    reqs.extend([
        _text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("primary", "#0077B6")),
    ])
    # Body text color + white background box on dark backgrounds
    body_text = "#222222" if _is_dark(theme.get("background", "#1e1e1e")) else theme.get("secondary", "#333333")
    if bullet_text:
        reqs.append(_text_style_req(f"{slide_id}_body", font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 20), bold=False, color_hex=body_text))
        reqs.append(_paragraph_style_req(f"{slide_id}_body", line_spacing=120))
    # White fill behind body text for readability
    reqs.append({
        "updateShapeProperties": {
            "objectId": f"{slide_id}_body",
            "shapeProperties": {
                "shapeBackgroundFill": {
                    "solidFill": {
                        "color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")},
                        "alpha": 1,
                    }
                }
            },
            "fields": "shapeBackgroundFill.solidFill.color"
        }
    })
    return reqs


def _insert_table(
    slide_id: str,
    title: str,
    header_left: str,
    header_right: str,
    left_points: List[str],
    right_points: List[str],
    theme: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Create a 2-column table (Benefits vs Challenges) with ≥6 rows.
    This more closely matches the provided screenshot style.
    """
    # Enforce ≥5 rows (per review). Do not create filler text; pad with blanks.
    left_points = (left_points or [])[:]
    right_points = (right_points or [])[:]
    min_rows = 5
    if len(left_points) < min_rows:
        left_points += [""] * (min_rows - len(left_points))
    if len(right_points) < min_rows:
        right_points += [""] * (min_rows - len(right_points))

    rows = max(len(left_points), len(right_points)) + 1  # +1 for header
    cols = 2
    table_id = f"{slide_id}_table"
    reqs: List[Dict[str, Any]] = [
        # Title
        {
            "createShape": {
                "objectId": f"{slide_id}_title",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 30, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_title", "text": title}},
        _text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("accent", "#ff6f3c")),
        # Table
        {
            "createTable": {
                "objectId": table_id,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 260, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 110, "unit": "PT"}
                },
                "rows": rows,
                "columns": cols,
            }
        },
    ]

    # Header row
    reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": 0, "columnIndex": 0}, "text": header_left}})
    reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": 0, "columnIndex": 1}, "text": header_right}})
    # Body rows
    for i in range(rows - 1):
        reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": i + 1, "columnIndex": 0}, "text": left_points[i] if i < len(left_points) else ""}})
        reqs.append({"insertText": {"objectId": table_id, "cellLocation": {"rowIndex": i + 1, "columnIndex": 1}, "text": right_points[i] if i < len(right_points) else ""}})

    # Contrast-aware styling: header filled with accent + white text; body cells white fill + dark text
    body_text = "#222222"
    header_fill = theme.get("accent", "#ff6f3c")
    # Fills
    for c in range(cols):
        # Header row fill
        reqs.append({
            "updateTableCellProperties": {
                "objectId": table_id,
                "tableRange": {"location": {"rowIndex": 0, "columnIndex": c}, "rowSpan": 1, "columnSpan": 1},
                "tableCellProperties": {
                    "tableCellBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _hex_to_rgb_dict(header_fill)}, "alpha": 1}
                    }
                },
                "fields": "tableCellBackgroundFill.solidFill.color",
            }
        })
        # Header text white
        reqs.append({
            "updateTextStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": c},
                "textRange": {"type": "ALL"},
                "style": {"foregroundColor": {"opaqueColor": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")}}},
                "fields": "foregroundColor",
            }
        })
    # Body cells fill + text
    for r in range(1, rows):
        for c in range(cols):
            reqs.append({
                "updateTableCellProperties": {
                    "objectId": table_id,
                    "tableRange": {"location": {"rowIndex": r, "columnIndex": c}, "rowSpan": 1, "columnSpan": 1},
                    "tableCellProperties": {
                        "tableCellBackgroundFill": {
                            "solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")}, "alpha": 1}
                        }
                    },
                    "fields": "tableCellBackgroundFill.solidFill.color",
                }
            })
            # Only apply text style when the cell has content; Slides rejects
            # updateTextStyle for empty cells.
            cell_text = (left_points[r-1] if c == 0 else right_points[r-1]) if (r-1) < len(left_points if c == 0 else right_points) else ""
            if isinstance(cell_text, str) and cell_text.strip():
                reqs.append({
                    "updateTextStyle": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r, "columnIndex": c},
                        "textRange": {"type": "ALL"},
                        "style": {"foregroundColor": {"opaqueColor": {"rgbColor": _hex_to_rgb_dict(body_text)}}},
                        "fields": "foregroundColor",
                    }
                })
    # Header bold
    reqs.extend([
        {
            "updateTextStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": 0},
                "textRange": {"type": "ALL"},
                "style": {"bold": True},
                "fields": "bold",
            }
        },
        {
            "updateTextStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": 1},
                "textRange": {"type": "ALL"},
                "style": {"bold": True},
                "fields": "bold",
            }
        },
    ])

    return reqs


def _insert_two_columns(
    slide_id: str,
    title: str,
    left_heading: str,
    left_points: List[str],
    right_heading: str,
    right_points: List[str],
    theme: Dict[str, str],
) -> List[Dict[str, Any]]:
    left_points = left_points or []
    right_points = right_points or []
    # Clean points; do not synthesize filler text
    left_points = [p for p in (left_points or []) if isinstance(p, str) and p.strip()]
    right_points = [p for p in (right_points or []) if isinstance(p, str) and p.strip()]
    left_text = "\n".join(left_points)
    right_text = "\n".join(right_points)
    # Layout tuned to fit within 640pt canvas
    left_x = 50
    right_x = 360
    col_w = 260
    title_y = 40
    heading_y = 100
    body_y = 140

    reqs: List[Dict[str, Any]] = [
        # Title
        {
            "createShape": {
                "objectId": f"{slide_id}_title",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": title_y, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_title", "text": title}},
    ]

    # Headings: hide if placeholders ("Left"/"Right")
    show_left_h = bool((left_heading or "").strip() and (left_heading or "").strip().lower() not in ("left",))
    show_right_h = bool((right_heading or "").strip() and (right_heading or "").strip().lower() not in ("right",))

    if show_left_h:
        reqs.extend([
            {
                "createShape": {
                    "objectId": f"{slide_id}_left_h",
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {"height": {"magnitude": 30, "unit": "PT"}, "width": {"magnitude": col_w, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": left_x, "translateY": heading_y, "unit": "PT"}
                    }
                }
            },
            {"insertText": {"objectId": f"{slide_id}_left_h", "text": left_heading}},
        ])
    # Left body
    reqs.extend([
        {
            "createShape": {
                "objectId": f"{slide_id}_left",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 260, "unit": "PT"}, "width": {"magnitude": col_w, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": left_x, "translateY": body_y, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_left", "text": left_text}},
        {"createParagraphBullets": {"objectId": f"{slide_id}_left", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
    ])
    if show_right_h:
        reqs.extend([
            {
                "createShape": {
                    "objectId": f"{slide_id}_right_h",
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {"height": {"magnitude": 30, "unit": "PT"}, "width": {"magnitude": col_w, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": right_x, "translateY": heading_y, "unit": "PT"}
                    }
                }
            },
            {"insertText": {"objectId": f"{slide_id}_right_h", "text": right_heading}},
        ])
    # Right body
    reqs.extend([
        {
            "createShape": {
                "objectId": f"{slide_id}_right",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 260, "unit": "PT"}, "width": {"magnitude": col_w, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": right_x, "translateY": body_y, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_right", "text": right_text}},
        {"createParagraphBullets": {"objectId": f"{slide_id}_right", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
    ])
    reqs.extend([
        _text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("accent", "#ff6f3c")),
    ])
    if show_left_h:
        reqs.append(_text_style_req(f"{slide_id}_left_h", font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 20), bold=True, color_hex=theme.get("primary", "#ffffff")))
    if show_right_h:
        reqs.append(_text_style_req(f"{slide_id}_right_h", font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 20), bold=True, color_hex=theme.get("primary", "#ffffff")))
    # Left/right bodies: dark text + white fill for readability
    body_text = "#222222" if _is_dark(theme.get("background", "#1e1e1e")) else theme.get("secondary", "#333333")
    for obj_id in (f"{slide_id}_left", f"{slide_id}_right"):
        reqs.append(_text_style_req(obj_id, font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 18), bold=False, color_hex=body_text))
        reqs.append(_paragraph_style_req(obj_id, line_spacing=120))
        reqs.append({
            "updateShapeProperties": {
                "objectId": obj_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")}, "alpha": 1}
                    }
                },
                "fields": "shapeBackgroundFill.solidFill.color"
            }
        })
    return reqs


def _insert_table_text(slide_id: str, title: str, table_text: str, theme: Dict[str, str]) -> List[Dict[str, Any]]:
    reqs: List[Dict[str, Any]] = [
        {
            "createShape": {
                "objectId": f"{slide_id}_title",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 60, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 40, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_title", "text": title}},
        {
            "createShape": {
                "objectId": f"{slide_id}_body",
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 300, "unit": "PT"}, "width": {"magnitude": 600, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 120, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": f"{slide_id}_body", "text": table_text}},
    ]
    reqs.extend([
        _text_style_req(f"{slide_id}_title", font=theme.get("font", "Inter"), size_pt=theme.get("subtitle_size", 28), bold=True, color_hex=theme.get("accent", "#ff6f3c")),
        _text_style_req(f"{slide_id}_body", font=theme.get("font", "Inter"), size_pt=theme.get("body_size", 18), bold=False, color_hex=theme.get("secondary", "#e6e6e6")),
    ])
    return reqs


def _hex_to_rgb_dict(hex_str: str) -> Dict[str, float]:
    """Convert "#RRGGBB" → {r:0-1,g:0-1,b:0-1}."""
    hex_str = hex_str.lstrip("#")
    return {
        "red": int(hex_str[0:2], 16) / 255.0,
        "green": int(hex_str[2:4], 16) / 255.0,
        "blue": int(hex_str[4:6], 16) / 255.0,
    }

def _is_dark(hex_str: str) -> bool:
    """Rudimentary luminance check to decide contrast fallback."""
    rgb = _hex_to_rgb_dict(hex_str)
    # Relative luminance approximation
    lum = 0.2126 * rgb["red"] + 0.7152 * rgb["green"] + 0.0722 * rgb["blue"]
    return lum < 0.5

# ---------------------------------------------------------------------------
# Global helper for applying consistent text style
# ---------------------------------------------------------------------------

def _text_style_req(obj_id: str, *, font: str, size_pt: int, bold: bool, color_hex: str) -> Dict[str, Any]:
    """Return an `updateTextStyle` request for the entire text range of *obj_id*."""
    return {
        "updateTextStyle": {
            "objectId": obj_id,
            "textRange": {"type": "ALL"},
            "style": {
                "fontFamily": font,
                "fontSize": {"magnitude": size_pt, "unit": "PT"},
                "bold": bold,
                "foregroundColor": {
                    "opaqueColor": {"rgbColor": _hex_to_rgb_dict(color_hex)}
                },
            },
            "fields": "fontFamily,fontSize,bold,foregroundColor",
        }
    }


def _paragraph_style_req(obj_id: str, *, line_spacing: int = 120) -> Dict[str, Any]:
    """Return an `updateParagraphStyle` request to adjust line spacing.

    line_spacing is a percentage integer (e.g., 120 means 120%).
    """
    return {
        "updateParagraphStyle": {
            "objectId": obj_id,
            "textRange": {"type": "ALL"},
            "style": {"lineSpacing": line_spacing},
            "fields": "lineSpacing",
        }
    }


def _insert_visual_hint(slide_id: str, hint: str, theme: Dict[str, str]) -> List[Dict[str, Any]]:
    """Render a subtle visual placeholder note on the slide using theme-aware styling."""
    obj_id = f"{slide_id}_hint"
    fg = theme.get("secondary", "#6B7280")
    font = theme.get("font", "Inter")
    reqs: List[Dict[str, Any]] = [
        {
            "createShape": {
                "objectId": obj_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"height": {"magnitude": 28, "unit": "PT"}, "width": {"magnitude": 560, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 420, "unit": "PT"}
                }
            }
        },
        {"insertText": {"objectId": obj_id, "text": f"Visual: {hint}"}},
        _text_style_req(obj_id, font=font, size_pt=14, bold=False, color_hex=fg),
        {
            "updateParagraphStyle": {
                "objectId": obj_id,
                "textRange": {"type": "ALL"},
                "style": {"lineSpacing": 110},
                "fields": "lineSpacing",
            }
        },
        {
            "updateShapeProperties": {
                "objectId": obj_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {
                            "color": {"rgbColor": _hex_to_rgb_dict("#FFFFFF")},
                            "alpha": 0.85,
                        }
                    },
                    "outline": {
                        "outlineFill": {"solidFill": {"color": {"rgbColor": _hex_to_rgb_dict("#E5E7EB")}}},
                        "weight": {"magnitude": 1, "unit": "PT"},
                    },
                },
                "fields": "shapeBackgroundFill.solidFill.color,outline.outlineFill.solidFill.color,outline.weight"
            }
        },
    ]
    return reqs
