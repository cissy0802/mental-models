#!/usr/bin/env python3
"""Bake Volcengine (Doubao) TTS mp3s for all readable segments.

Walks HTML pages, extracts plain text from data-zh / data-en attributes on
segment-level elements (h1/h2/h3/p), hashes each segment (sha1[:16]),
writes the hash back as data-tts-zh / data-tts-en on the element, and
generates audio/<lang>/<hash>.mp3 via the Volcengine openspeech API.

Idempotent: skips audio files that already exist; only rewrites HTML when
attributes actually change. Safe to re-run after content edits — only the
new hashes get baked.

Env vars:
    VOLCANO_APP_ID         required  火山控制台 → 语音技术 → 应用管理
    VOLCANO_API_KEY        required  per-app access token (UUID-style),
                                     NOT the IAM AKLT... key
    VOLCANO_VOICE_ID       required  Chinese voice (S_xxx = cloned voice
                                     → cluster=volcano_icl; otherwise
                                     preset → cluster=volcano_tts)
    VOLCANO_VOICE_ID_EN    optional  English voice; if absent, EN baking
                                     is skipped (Web Speech fallback)

Usage:
    pip install beautifulsoup4 requests
    python3 bake-tts.py                              # all *-dayNN.html
    python3 bake-tts.py decision-making-day01.html   # one page
    python3 bake-tts.py --lang zh                    # only Chinese
    python3 bake-tts.py --dry-run                    # plan, no API calls
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
# Elements whose data-zh/data-en text becomes part of a model's narration.
# We DELIBERATELY include div (for prompt-item) so AI prompts are read aloud.
NARRATION_TAGS = ("h1", "h2", "h3", "h4", "p", "div", "li")
REPO_DIR = Path(__file__).parent.resolve()
AUDIO_DIR = REPO_DIR / "audio"
MAX_CHARS_PER_CALL = 280  # Volcano per-call limit ≈ 1024 bytes UTF-8


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def plain_text(attr_value: str) -> str:
    """Strip inline HTML (<br>, <strong>, &ldquo;, …) from an attribute value
    so the hash matches what the browser sees as element.textContent."""
    return BeautifulSoup(attr_value, "html.parser").get_text().strip()


def normalize_for_tts(text: str) -> str:
    """Replace characters Volcano TTS sometimes chokes on (smart quotes,
    em-dash, nested quotes around ASCII words). Pure text munging, doesn't
    affect the hash (called after hashing)."""
    return (
        text
        .replace("’", "'")  # right single quote
        .replace("‘", "'")  # left single quote
        .replace("“", '"')  # left double quote
        .replace("”", '"')  # right double quote
        .replace("—", " - ")  # em dash
        .replace("–", "-")    # en dash
        .replace("…", "...")  # ellipsis
        .replace(" ", " ")    # nbsp
    )


def pick_cluster(voice_id: str) -> str:
    return "volcano_icl" if voice_id.startswith("S_") else "volcano_tts"


def synth(app_id: str, api_key: str, voice_id: str, text: str) -> bytes:
    body = {
        "app": {"appid": app_id, "token": api_key, "cluster": pick_cluster(voice_id)},
        "user": {"uid": "mental-models-daily"},
        "audio": {
            "voice_type": voice_id,
            "encoding": "mp3",
            "speed_ratio": 1.0,
            "rate": 24000,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "operation": "query",
        },
    }
    headers = {
        "Authorization": f"Bearer;{api_key}",  # NOTE: semicolon, not space
        "Content-Type": "application/json",
    }
    r = requests.post(ENDPOINT, json=body, headers=headers, timeout=30)
    try:
        payload = r.json()
    except Exception:
        raise RuntimeError(f"HTTP {r.status_code} — non-JSON response: {r.text[:500]}")

    code = payload.get("code")
    if r.status_code != 200 or code != 3000:
        raise RuntimeError(
            f"TTS failed (HTTP {r.status_code}, code={code}): "
            f"{json.dumps(payload, ensure_ascii=False)[:800]}"
        )
    data_b64 = payload.get("data")
    if not data_b64:
        raise RuntimeError(f"No audio data in response: {json.dumps(payload)[:500]}")
    return base64.b64decode(data_b64)


def collect_groups(soup) -> list[tuple]:
    """Return [(anchor_element, {'zh': text, 'en': text}), ...] grouped by model.

    A 'model' is bounded by h2 elements. The very first group (before the first
    h2) is the cover (h1 + intro). Each group concatenates all data-zh / data-en
    text from descendant elements between two consecutive h2 boundaries — so
    paragraphs, AI prompt items, list items etc. all roll up into ONE audio per
    model. The hash is written back to the anchor (h1 for cover, h2 for models).
    """
    body = soup.body or soup
    h2s = [h for h in body.find_all("h2") if not h.find_parent(class_="mmd-controls")]
    if not h2s:
        return []  # no model boundaries — page isn't a content page

    # Bounds: each model's range is [its h2 .. next h2)
    # Cover's range is [body start .. first h2)
    h1 = body.find("h1")
    anchors_and_bounds = []
    anchors_and_bounds.append((h1 or h2s[0], None, h2s[0]))  # cover
    for i, h2 in enumerate(h2s):
        end = h2s[i + 1] if i + 1 < len(h2s) else None
        anchors_and_bounds.append((h2, h2, end))

    def in_range(el, start, end) -> bool:
        # Element is in [start, end). If start is None, just "before end".
        if el.find_parent("nav") or el.find_parent(class_="mmd-controls"):
            return False
        if start is not None and el is not start:
            # walk previous siblings/ancestors to see if we passed `start`
            pass  # cheaper to just check positionally below
        return True

    # Use document-order comparison via .sourcepos isn't available in bs4,
    # so we walk the document in order and assign each tagged element to a range.
    # Decorative elements to skip: their data-zh/data-en contain the OPPOSITE
    # language text (used as a "show the other language as subtitle" device),
    # which would make e.g. the English voice try to pronounce Chinese chars.
    SKIP_CLASSES = {"en", "zh", "date", "category"}

    def is_decorative(el) -> bool:
        cls = el.get("class") or []
        return any(c in SKIP_CLASSES for c in cls)

    all_tagged = []
    for tag in NARRATION_TAGS:
        for el in body.find_all(tag):
            if el.find_parent("nav") or el.find_parent(class_="mmd-controls"):
                continue
            if is_decorative(el):
                continue
            if not (el.has_attr("data-zh") or el.has_attr("data-en")):
                continue
            all_tagged.append(el)

    # Build a doc-order index for h2 anchors
    h2_index = {id(h): i for i, h in enumerate(h2s)}
    # Result accumulator: group_idx → {lang: [text, text, ...]}
    n_groups = len(anchors_and_bounds)
    bins: list[dict] = [{"zh": [], "en": []} for _ in range(n_groups)]

    # Also pick up <p> inside .prompt-item which lacks data-zh/data-en — these
    # are language-specific prompts (Chinese version vs English version), not
    # translations. We assign each to its language bucket by content detection.
    def detect_lang(text: str) -> str:
        # If any CJK char, treat as zh; otherwise en
        return "zh" if re.search(r"[一-鿿]", text) else "en"

    prompt_ps = []
    for pi in body.find_all(class_="prompt-item"):
        for p in pi.find_all("p"):
            if p.find_parent("nav") or p.find_parent(class_="mmd-controls"):
                continue
            if p.has_attr("data-zh") or p.has_attr("data-en"):
                continue  # already covered by attribute-based logic above
            txt = p.get_text().strip()
            if txt:
                prompt_ps.append((p, detect_lang(txt), txt))

    # Walk doc-order list of tagged elements; track current group index.
    # An element belongs to group `g` iff it appears after group g's anchor
    # but before group g+1's anchor (h2). We compute by counting h2s seen.
    doc_order = list(body.descendants)
    h2_seen = 0
    h2_set = set(id(h) for h in h2s)
    tagged_set = set(id(e) for e in all_tagged)
    prompt_p_map = {id(p): (lang, txt) for p, lang, txt in prompt_ps}
    for node in doc_order:
        if id(node) in h2_set:
            h2_seen += 1
        if id(node) in tagged_set:
            # Group 0 is cover (h2_seen == 0); group i (i≥1) is model i (h2_seen == i)
            g = h2_seen
            for lang in ("zh", "en"):
                if node.has_attr(f"data-{lang}"):
                    t = plain_text(node[f"data-{lang}"])
                    if t:
                        bins[g][lang].append(t)
        elif id(node) in prompt_p_map:
            g = h2_seen
            lang, txt = prompt_p_map[id(node)]
            bins[g][lang].append(txt)

    # Build result
    out = []
    for (anchor, _, _), texts in zip(anchors_and_bounds, bins):
        joined = {lang: "  ".join(texts[lang]).strip() for lang in ("zh", "en")}
        if joined["zh"] or joined["en"]:
            out.append((anchor, joined))
    return out


def chunk_text(text: str, max_chars: int = MAX_CHARS_PER_CALL) -> list[str]:
    """Split text into chunks ≤ max_chars at sentence boundaries (。！？.!?)."""
    if len(text) <= max_chars:
        return [text]
    # Split keeping punctuation
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if not p:
            continue
        if len(cur) + len(p) > max_chars and cur:
            chunks.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        chunks.append(cur)
    # Hard-split any chunk that's still too long (very long sentence with no punct)
    final = []
    for c in chunks:
        while len(c) > max_chars:
            final.append(c[:max_chars])
            c = c[max_chars:]
        if c:
            final.append(c)
    return final


def synth_with_retry(app_id, api_key, voice_id, text, max_retries=3):
    """Call synth with exponential backoff for transient 'engine process fail'."""
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            return synth(app_id, api_key, voice_id, normalize_for_tts(text))
        except RuntimeError as e:
            msg = str(e)
            # Only retry transient engine errors, not auth/quota
            if "3031" in msg or "engine process fail" in msg or "HTTP 5" in msg:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_err


def synth_long(app_id: str, api_key: str, voice_id: str, text: str) -> bytes:
    """TTS arbitrary-length text by chunking + ffmpeg concat (so the final mp3
    has a proper VBR header and the browser reports a finite audio.duration —
    raw binary concat leaves duration=Infinity which breaks seeking)."""
    import time
    import subprocess
    import tempfile
    chunks = chunk_text(text)
    if len(chunks) == 1:
        return synth_with_retry(app_id, api_key, voice_id, chunks[0])

    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, c in enumerate(chunks):
            try:
                mp3 = synth_with_retry(app_id, api_key, voice_id, c)
            except Exception as e:
                print(f"        chunk {i}/{len(chunks)} ({len(c)} chars) failed: {c[:60]!r}", file=sys.stderr)
                raise
            f = Path(tmp) / f"part{i:03d}.mp3"
            f.write_bytes(mp3)
            files.append(f)
            time.sleep(0.3)  # gentle pacing

        # ffmpeg concat demuxer expects a manifest file
        manifest = Path(tmp) / "list.txt"
        manifest.write_text("".join(f"file '{f}'\n" for f in files))
        out = Path(tmp) / "out.mp3"
        # -c copy = no re-encoding (fast, lossless); ffmpeg fixes the header.
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(manifest), "-c", "copy", str(out)],
            capture_output=True,
        )
        if result.returncode != 0:
            # Fallback: re-encode to be safe
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(manifest), "-c:a", "libmp3lame", "-b:a", "128k",
                 str(out)],
                capture_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode()[:500]}")
        return out.read_bytes()


def process_page(
    path: Path,
    app_id: str,
    api_key: str,
    voice_zh: str,
    voice_en: str | None,
    langs: set,
    dry_run: bool,
) -> None:
    print(f"\n=== {path.name} ===")
    html_src = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_src, "html.parser")
    groups = collect_groups(soup)
    print(f"  {len(groups)} model groups (cover + N models)")

    changed = False
    generated = skipped_existing = skipped_lang = errors = 0

    # Clean up old per-paragraph data-tts-* attributes — only anchors should keep them
    anchor_ids = {id(a) for a, _ in groups}
    for el in soup.find_all(attrs={"data-tts-zh": True}):
        if id(el) not in anchor_ids:
            del el["data-tts-zh"]
            changed = True
    for el in soup.find_all(attrs={"data-tts-en": True}):
        if id(el) not in anchor_ids:
            del el["data-tts-en"]
            changed = True

    for anchor, lang_texts in groups:
        for lang in ("zh", "en"):
            if lang not in langs:
                skipped_lang += 1
                continue
            text = lang_texts.get(lang, "")
            if not text:
                continue
            voice = voice_zh if lang == "zh" else voice_en
            if not voice and not dry_run:
                skipped_lang += 1
                continue

            digest = hash_text(text)
            attr_name = f"data-tts-{lang}"
            if anchor.get(attr_name) != digest:
                anchor[attr_name] = digest
                changed = True

            mp3_path = AUDIO_DIR / lang / f"{digest}.mp3"
            label = (anchor.get_text() or "(cover)")[:20]
            if mp3_path.exists():
                skipped_existing += 1
                continue
            if dry_run:
                print(f"  [{lang}] would bake {digest}.mp3 ← {label} ({len(text)} chars)")
                continue
            try:
                audio = synth_long(app_id, api_key, voice, text)
                mp3_path.parent.mkdir(parents=True, exist_ok=True)
                mp3_path.write_bytes(audio)
                generated += 1
                n_chunks = len(chunk_text(text))
                print(f"  [{lang}] {digest}.mp3 ({len(audio):,} B, {n_chunks} chunks) ← {label}")
            except Exception as e:
                errors += 1
                print(f"  [{lang}] FAILED {digest} ← {label}\n         {e}", file=sys.stderr)

    if changed and not dry_run:
        path.write_text(str(soup), encoding="utf-8")
        print(f"  wrote {path.name} (updated data-tts-* attributes)")

    print(
        f"  generated={generated} skipped_existing={skipped_existing} "
        f"skipped_lang={skipped_lang} errors={errors}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="HTML files (default: all *-dayNN.html)")
    parser.add_argument("--lang", choices=["zh", "en", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    app_id = os.environ.get("VOLCANO_APP_ID")
    api_key = os.environ.get("VOLCANO_API_KEY")
    voice_zh = os.environ.get("VOLCANO_VOICE_ID")
    voice_en = os.environ.get("VOLCANO_VOICE_ID_EN")

    if not args.dry_run:
        missing = [
            k for k, v in {
                "VOLCANO_APP_ID": app_id,
                "VOLCANO_API_KEY": api_key,
                "VOLCANO_VOICE_ID": voice_zh,
            }.items() if not v
        ]
        if missing:
            print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)
    if "en" in (args.lang, "all") and not voice_en:
        print("Note: VOLCANO_VOICE_ID_EN not set — English segments will be skipped.")

    langs = {"zh", "en"} if args.lang == "all" else {args.lang}

    if args.files:
        files = [Path(f) if Path(f).is_absolute() else REPO_DIR / f for f in args.files]
    else:
        files = sorted(
            p for p in REPO_DIR.iterdir() if re.match(r".+-day\d+\.html$", p.name)
        )

    for path in files:
        try:
            process_page(path, app_id, api_key, voice_zh, voice_en, langs, args.dry_run)
        except Exception as e:
            print(f"  PAGE FAILED: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
