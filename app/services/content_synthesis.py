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
