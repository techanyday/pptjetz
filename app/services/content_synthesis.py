import json
import os
import re
from collections import Counter
from typing import List, Dict

# Phrases we must never render
BANNED_PHRASES = {
    "brief explanation", "typical impact", "to be refined", "lorem ipsum",
    "placeholder", "sample text", "generic description", "concise, topic-aware"
}

# Backwards-compat export
BANNED = BANNED_PHRASES

# Titles like "Concept 1" are not allowed
TITLE_BANNED_RE = re.compile(r"^\s*concepts?\s*\d+\s*$", re.I)

# Lightweight topic terms from user signals
STOP = {"the","and","for","with","from","that","this","your","their","into","about",
        "data","information","system","systems","solution","approach","technique",
        "concept","topic","example","analysis","analytics","model","models","process",
        "insight","insights","business","value","impact"}

BENEFITS = [
    "reduce costs","improve accuracy","increase speed","boost roi","cut churn",
    "raise conversion","streamline operations","shorten cycle times","improve cx",
    "enhance reliability","increase compliance"
]

IMAGE_GENERIC_TITLES = {
    "future opportunities",
    "implementation priorities",
    "operating model changes",
    "visual insight",
}

IMAGE_DOMAIN_FOCUS = [
    (("cinema", "film", "movie", "screen"), "story development, production planning, editing, and audience reach"),
    (("5g", "telecom", "network", "connectivity"), "connected devices, low-latency services, and real-time operations"),
    (("healthcare", "medical", "hospital", "clinical"), "triage, diagnostics, scheduling, and care coordination"),
    (("education", "learning", "classroom", "student"), "lesson design, feedback, and learner support"),
    (("finance", "bank", "banking", "fintech", "insurance"), "risk review, fraud monitoring, underwriting, and service decisions"),
    (("retail", "commerce", "ecommerce", "shopping"), "forecasting, merchandising, personalization, and fulfillment"),
    (("manufacturing", "factory", "industrial", "supply chain"), "quality control, maintenance, throughput planning, and inventory flow"),
    (("logistics", "transport", "shipping", "delivery"), "routing, visibility, exception handling, and capacity planning"),
    (("sales", "revenue", "pipeline", "prospecting"), "qualification, account prioritization, pipeline movement, and win-rate improvement"),
    (("marketing", "campaign", "brand", "advertising"), "targeting, creative testing, budget allocation, and conversion improvement"),
]

IMAGE_MECHANISMS = [
    (("ai", "artificial intelligence", "machine learning", "generative"), "It tends to help most with faster research, pattern recognition, and repetitive production work while expert judgment stays in the loop."),
    (("5g", "telecom", "network", "connectivity"), "Higher bandwidth and lower latency make those workflows more dependable when timing, mobility, or dense device activity matter."),
    (("automation", "robot", "robotics"), "Its main advantage usually comes from reducing manual handoffs, standardizing execution, and keeping throughput more consistent."),
    (("analytics", "data", "insight", "forecast"), "Its value grows when teams can turn scattered signals into faster decisions, earlier warnings, and more confident prioritization."),
]


def _clean_image_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" ,.;:-")


def _normalize_image_detail_phrase(text: str) -> str:
    clean = _clean_image_text(text)
    if len(clean) > 1 and clean[0].isupper() and clean[1:].lower() == clean[1:]:
        clean = clean[0].lower() + clean[1:]
    return clean


def _join_image_parts(parts: List[str]) -> str:
    items = [_clean_image_text(p) for p in parts if _clean_image_text(p)]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _strip_image_title_prefixes(text: str) -> str:
    cleaned = _clean_image_text(text)
    if not cleaned:
        return ""
    return re.sub(
        r"^(visualizing|exploring|examining|understanding|analyzing|mapping|illustrating|showing)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )


def _extract_image_subject_context(title: str, query: str) -> tuple[str, str]:
    title_clean = _strip_image_title_prefixes(title)
    query_clean = _strip_image_title_prefixes(query)
    use_query_first = bool(query_clean and title_clean.lower() in IMAGE_GENERIC_TITLES)
    primary = query_clean if use_query_first else (title_clean or query_clean)
    candidates: List[str] = []
    for raw in [primary, query_clean, title_clean]:
        cleaned = _clean_image_text(raw)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
        for part in re.split(r"[:–—|]", cleaned):
            part_clean = _clean_image_text(part)
            if part_clean and part_clean not in candidates:
                candidates.append(part_clean)

    patterns = [
        r"^(?P<subject>.+?)'s\s+(?:role|impact|influence|effect|potential)\s+in\s+(?P<context>.+)$",
        r"^(?:the\s+)?(?:role|impact(?:\s+and\s+potential)?|influence|effect|use|applications|benefits|future|potential|rise|adoption|evolution|importance|business applications)\s+of\s+(?P<subject>.+?)\s+in\s+(?P<context>.+)$",
        r"^(?:the\s+)?(?:role|impact(?:\s+and\s+potential)?|influence|effect|use|applications|benefits|future|potential|rise|adoption|evolution|importance|business applications)\s+of\s+(?P<subject>.+)$",
        r"^(?P<subject>.+?)\s+in\s+(?P<context>.+)$",
        r"^(?P<subject>.+?)\s+for\s+(?P<context>.+)$",
    ]

    for raw in candidates:
        if not raw:
            continue
        for pattern in patterns:
            match = re.match(pattern, raw, flags=re.IGNORECASE)
            if not match:
                continue
            subject = _clean_image_text(match.groupdict().get("subject", ""))
            context = _clean_image_text(match.groupdict().get("context", ""))
            subject = re.sub(r"^(the|a|an)\s+", "", subject, flags=re.IGNORECASE)
            context = re.sub(r"^(the|a|an)\s+", "", context, flags=re.IGNORECASE)
            if subject:
                return subject, context

    return _clean_image_text(primary) or "this topic", ""


def _extract_image_detail_phrases(bullets: List[str] | None, draft_caption: str, subject: str, context: str) -> List[str]:
    raw_items: List[str] = []
    for item in bullets or []:
        raw_items.append(str(item or ""))

    draft = _clean_image_text(draft_caption)
    if draft:
        parts = re.split(r",|;|\band\b", draft)
        if len(parts) <= 1:
            parts = re.split(r"(?<=[.!?])\s+", draft)
        raw_items.extend(parts)

    skip_terms = {subject.lower(), context.lower()}
    skip_substrings = (
        "this visual",
        "the slide",
        "this slide",
        "matters most",
        "creates the most value",
        "becomes meaningful",
        "clear business goals",
        "operating contexts",
    )
    details: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        clean = re.sub(r"^[•\-–—\d.) ]+", "", str(item or "").strip())
        clean = _normalize_image_detail_phrase(clean)
        low = clean.lower()
        if not clean or low in seen or low in skip_terms:
            continue
        if any(part in low for part in skip_substrings):
            continue
        if len(clean.split()) > 6:
            continue
        seen.add(low)
        details.append(clean)
        if len(details) >= 4:
            break
    return details


def _lookup_image_fragment(text: str, table: List[tuple[tuple[str, ...], str]]) -> str:
    low = text.lower()
    for keywords, phrase in table:
        if any(keyword in low for keyword in keywords):
            return phrase
    return ""


def _pick_image_theme(title: str) -> str:
    low = title.lower()
    if any(token in low for token in ("future", "opportunit", "outlook", "next")):
        return "future"
    if any(token in low for token in ("implementation", "operating model", "priorit", "adoption", "rollout", "execution")):
        return "implementation"
    if any(token in low for token in ("role", "impact", "influence", "effect")):
        return "impact"
    return "general"


def _limit_image_caption_words(text: str, max_words: int = 58) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    kept: List[str] = []
    count = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if kept and count + sentence_words > max_words:
            break
        kept.append(sentence)
        count += sentence_words
    if kept:
        return " ".join(kept).strip()
    trimmed = " ".join(words[:max_words]).rstrip(" ,.;:-")
    return f"{trimmed}."


def compose_image_caption(title: str, *, query: str = "", bullets: List[str] | None = None, draft_caption: str = "") -> str:
    title_clean = _clean_image_text(title)
    query_clean = _clean_image_text(query)
    subject, context = _extract_image_subject_context(title_clean, query_clean)
    details = _extract_image_detail_phrases(bullets, draft_caption, subject, context)
    signal_text = " ".join([title_clean, query_clean, subject, context, " ".join(details)]).strip()
    focus_area = _join_image_parts(details[:3]) or _lookup_image_fragment(signal_text, IMAGE_DOMAIN_FOCUS)
    if not focus_area:
        focus_area = "one measurable workflow, faster feedback, and stronger execution"
    mechanism = _lookup_image_fragment(signal_text, IMAGE_MECHANISMS)
    theme = _pick_image_theme(title_clean or query_clean)

    if theme == "future":
        opener = f"Looking ahead, the strongest opportunities for {subject} will come from {focus_area}."
        closer = "Teams that validate demand early and scale from real evidence are usually better positioned than teams that invest on hype alone."
    elif theme == "implementation":
        opener = f"Execution improves when {subject} is tied to {focus_area} instead of framed as a broad initiative."
        closer = "Clear ownership, adoption support, and review loops usually matter more than the technology alone."
    elif context:
        opener = f"In {context}, {subject} is starting to reshape {focus_area}."
        closer = "The strongest results come from narrow pilots that solve a real workflow problem and generate evidence before broader adoption."
    else:
        opener = f"Adoption becomes more practical when {subject} supports {focus_area}."
        closer = "Results usually improve when teams measure one workflow carefully and expand only after the gains are proven."

    if mechanism:
        middle = mechanism
    elif details:
        middle = "That shift is easiest to see where speed, coordination, or consistency can improve without adding unnecessary complexity."
    else:
        middle = "The payoff is usually highest where teams can tie the capability to repeatable work, clearer decisions, or stronger customer outcomes."

    return _limit_image_caption_words(" ".join([opener, middle, closer]).strip())


def _topic_terms(points: List[str]) -> List[str]:
    txt = " ".join(points or [])
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", txt.lower())
    cand  = [w for w in words if w not in STOP]
    return [w for w,_ in Counter(cand).most_common(15)]

def is_placeholder(s: str) -> bool:
    if not s:
        return True
    low = s.lower().strip()
    if any(b in low for b in BANNED_PHRASES):
        return True
    # too short / too vague
    if len(low.split()) < 4:
        return True
    return False

def _score_one_liner(text: str, topic_terms: List[str]) -> int:
    low = text.lower()
    kscore = sum(1 for k in topic_terms if k in low)      # topical words
    bscore = sum(1 for b in BENEFITS if b in low)         # concrete benefit verbs
    return kscore + bscore                                # >= 2 required


def _call_openai(prompt_sys: str, prompt_user: str) -> Dict:
    """Call OpenAI if available. Returns parsed JSON object or {} on failure.
    Uses the new SDK style: from openai import OpenAI. Falls back gracefully if
    the package or API key is missing.
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return {}

    if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_B64") or os.getenv("OPENAI_API_BASE")):
        # No credentials configured
        return {}

    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            temperature=0.7,
            messages=[{"role": "system", "content": prompt_sys}, {"role": "user", "content": prompt_user}],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception:
        return {}


def synthesize_key_concepts(topic: str, source_points: List[str], n: int = 6) -> List[Dict[str, str]]:
    """Return up to n dicts: [{title, one_liner}] with strict filtering.
    Includes topical-term extraction, banned-title check, benefit scoring, and
    robust fallback derived from user bullets when API is absent.
    """
    topic_terms = _topic_terms([topic] + (source_points or []))

    sys = "Return only valid JSON. You write concrete, topic-specific microcopy."
    user = f"""
Create {n} concept cards for the topic.

Topic: {topic}
Key terms to weave in: {', '.join(topic_terms) or '[none]'}.

Rules:
- JSON only: {{"concepts":[{{"title":"...", "one_liner":"..."}}]}}
- title: \u2264 6 words, meaningful; forbid "Concept 1/2/\u2026", generic nouns.
- one_liner: 12\u201320 words; concrete, measurable language; no filler/boilerplate.
- Include at least TWO of: topical terms, concrete benefits (e.g., "reduce costs", "improve accuracy").
- Absolutely forbid phrases: {', '.join(BANNED_PHRASES)}.
""".strip()

    data = _call_openai(sys, user)
    items = data.get("concepts") if isinstance(data, dict) else None
    items = items or []

    cleaned: List[Dict[str, str]] = []
    seen = set()
    for c in items:
        t = (c.get("title") or "").strip()
        d = (c.get("one_liner") or "").strip()
        if not t or not d:
            continue
        if TITLE_BANNED_RE.match(t):
            continue
        if is_placeholder(t) or is_placeholder(d):
            continue
        tw = len(t.split()); dw = len(d.split())
        if not (1 <= tw <= 6 and 12 <= dw <= 20):
            continue
        if _score_one_liner(d, topic_terms) < 2:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo); cleaned.append({"title": t, "one_liner": d})
        if len(cleaned) >= n:
            break

    # Repair attempt
    if len(cleaned) < max(4, n):
        hint = (
            user
            + "\nSeveral items were rejected. Replace vague copy with concrete, topic-specific wording. Return JSON only."
        )
        data2 = _call_openai(sys, hint)
        for c in (data2.get("concepts") or []):
            t = (c.get("title") or "").strip()
            d = (c.get("one_liner") or "").strip()
            if not t or not d or TITLE_BANNED_RE.match(t):
                continue
            if is_placeholder(t) or is_placeholder(d):
                continue
            tw = len(t.split()); dw = len(d.split())
            if not (1 <= tw <= 6 and 12 <= dw <= 20):
                continue
            if _score_one_liner(d, topic_terms) < 2:
                continue
            lo = t.lower()
            if lo in seen:
                continue
            seen.add(lo); cleaned.append({"title": t, "one_liner": d})
            if len(cleaned) >= n:
                break

    # Fallback: synthesize from user bullets if API is weak/absent
    i = 0
    while len(cleaned) < n and i < len(source_points or []):
        s = (source_points[i] or "").strip(); i += 1
        if not s or TITLE_BANNED_RE.match(s) or is_placeholder(s):
            continue
        benefit = BENEFITS[(len(cleaned) + i) % len(BENEFITS)]
        title = re.sub(r"[:\-–].*$", "", s).title()[:48]
        line  = f"{s} to {benefit}; align owners, KPIs and timelines."
        if _score_one_liner(line, topic_terms) < 2:
            continue
        cleaned.append({"title": title, "one_liner": line})

    # Final fallback: derive from topic terms when no bullets/API
    if len(cleaned) < n and topic_terms:
        j = 0
        while len(cleaned) < n and j < len(topic_terms):
            term = topic_terms[j]
            benefit = BENEFITS[(len(cleaned) + j) % len(BENEFITS)]
            title = term.title()[:48]
            line = f"{topic} — leveraging {term} to {benefit} with measurable KPIs and owners."
            if _score_one_liner(line, topic_terms) >= 2 and not TITLE_BANNED_RE.match(title):
                cleaned.append({"title": title, "one_liner": line})
            j += 1

    return cleaned[:n]
