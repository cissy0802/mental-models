#!/usr/bin/env python3
"""Bake Azure Speech TTS mp3s for all readable segments.

Walks HTML pages, groups text by <h2> (per model), generates audio/<lang>/<hash>.mp3
via the Azure Cognitive Services TTS REST API, and writes data-tts-{lang}=<hash>
back to the anchor element (h1 for cover, h2 for each model).

Idempotent: skips audio files that already exist; only rewrites HTML when
attributes actually change.

Env vars:
    AZURE_SPEECH_KEY        required  Azure Speech resource key
    AZURE_SPEECH_REGION     required  e.g. "eastus", "eastus2"
    AZURE_VOICE_ID          optional  Chinese voice (default: zh-CN-XiaoxiaoNeural)
    AZURE_VOICE_ID_EN       optional  English voice; if absent, EN baking skipped

Usage:
    pip install beautifulsoup4 requests
    python3 bake-tts.py                              # all *-dayNN.html
    python3 bake-tts.py decision-making-day01.html   # one page
    python3 bake-tts.py --lang zh                    # only Chinese
    python3 bake-tts.py --dry-run                    # plan, no API calls
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Azure endpoint template — region is filled in at request time
ENDPOINT_TEMPLATE = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
DEFAULT_VOICE_ZH = "zh-CN-XiaoxiaoNeural"
DEFAULT_VOICE_EN = "en-US-JennyNeural"
# Elements whose data-zh/data-en text becomes part of a model's narration.
NARRATION_TAGS = ("h1", "h2", "h3", "h4", "p", "div", "li")
REPO_DIR = Path(__file__).parent.resolve()
AUDIO_DIR = REPO_DIR / "audio"
# Azure tolerates much larger bodies than Volcano. 3000 chars gives plenty of
# headroom under their ~10-min audio-per-request limit; most model sections fit
# in one call so ffmpeg concat isn't usually needed.
MAX_CHARS_PER_CALL = 3000


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def plain_text(attr_value: str) -> str:
    """Strip inline HTML from a data-* attribute so the hash matches what the
    browser sees as element.textContent."""
    return BeautifulSoup(attr_value, "html.parser").get_text().strip()


def normalize_for_tts(text: str) -> str:
    """Light normalization. Azure handles smart quotes / em-dash fine, but
    fixing nbsp etc. avoids weird pauses."""
    return text.replace(" ", " ")  # nbsp → regular space


def ssml_escape(text: str) -> str:
    """XML-escape user text before embedding in SSML."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def synth(key: str, region: str, voice_name: str, text: str) -> bytes:
    """Single Azure TTS call. Voice name like 'zh-CN-XiaoxiaoNeural'."""
    lang = "-".join(voice_name.split("-")[:2])  # e.g. zh-CN-XiaoxiaoNeural → zh-CN
    body = (
        f'<speak version="1.0" xml:lang="{lang}">'
        f'<voice name="{voice_name}">{ssml_escape(normalize_for_tts(text))}</voice>'
        f'</speak>'
    ).encode("utf-8")

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "mental-models-daily-bake",
    }
    url = ENDPOINT_TEMPLATE.format(region=region)
    r = requests.post(url, data=body, headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Azure TTS HTTP {r.status_code}: {r.text[:500]}")
    return r.content


def synth_with_retry(key, region, voice_name, text, max_retries=3):
    """Retry transient 5xx / 429 with exponential backoff."""
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            return synth(key, region, voice_name, text)
        except RuntimeError as e:
            msg = str(e)
            if "HTTP 5" in msg or "HTTP 429" in msg:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_err


def synth_long(key: str, region: str, voice_name: str, text: str) -> bytes:
    """TTS arbitrary-length text by chunking + ffmpeg concat. Each Azure call
    returns mp3 with proper headers, but concat still benefits from ffmpeg
    rewriting the header for the joined file (so audio.duration is finite)."""
    import time
    import subprocess
    import tempfile
    chunks = chunk_text(text)
    if len(chunks) == 1:
        return synth_with_retry(key, region, voice_name, chunks[0])

    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, c in enumerate(chunks):
            try:
                mp3 = synth_with_retry(key, region, voice_name, c)
            except Exception:
                print(f"        chunk {i}/{len(chunks)} ({len(c)} chars) failed: {c[:60]!r}", file=sys.stderr)
                raise
            f = Path(tmp) / f"part{i:03d}.mp3"
            f.write_bytes(mp3)
            files.append(f)
            time.sleep(0.1)

        manifest = Path(tmp) / "list.txt"
        manifest.write_text("".join(f"file '{f}'\n" for f in files))
        out = Path(tmp) / "out.mp3"
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(manifest), "-c", "copy", str(out)],
            capture_output=True,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(manifest), "-c:a", "libmp3lame", "-b:a", "128k",
                 str(out)],
                capture_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode()[:500]}")
        return out.read_bytes()


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

    h1 = body.find("h1")
    anchors_and_bounds = []
    anchors_and_bounds.append((h1 or h2s[0], None, h2s[0]))  # cover
    for i, h2 in enumerate(h2s):
        end = h2s[i + 1] if i + 1 < len(h2s) else None
        anchors_and_bounds.append((h2, h2, end))

    # Decorative elements whose data-zh/data-en contain OPPOSITE-language text.
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

    n_groups = len(anchors_and_bounds)
    bins: list[dict] = [{"zh": [], "en": []} for _ in range(n_groups)]

    # Pick up <p> inside .prompt-item which lacks data-zh/data-en (different
    # content per language, not translations).
    def detect_lang(text: str) -> str:
        return "zh" if re.search(r"[一-鿿]", text) else "en"

    prompt_ps = []
    for pi in body.find_all(class_="prompt-item"):
        for p in pi.find_all("p"):
            if p.find_parent("nav") or p.find_parent(class_="mmd-controls"):
                continue
            if p.has_attr("data-zh") or p.has_attr("data-en"):
                continue
            txt = p.get_text().strip()
            if txt:
                prompt_ps.append((p, detect_lang(txt), txt))

    doc_order = list(body.descendants)
    h2_seen = 0
    h2_set = set(id(h) for h in h2s)
    tagged_set = set(id(e) for e in all_tagged)
    prompt_p_map = {id(p): (lang, txt) for p, lang, txt in prompt_ps}
    for node in doc_order:
        if id(node) in h2_set:
            h2_seen += 1
        if id(node) in tagged_set:
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
    final = []
    for c in chunks:
        while len(c) > max_chars:
            final.append(c[:max_chars])
            c = c[max_chars:]
        if c:
            final.append(c)
    return final


def process_page(
    path: Path,
    key: str,
    region: str,
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

    # Clean up stale per-paragraph data-tts-* — only anchors should keep them
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
                audio = synth_long(key, region, voice, text)
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

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    voice_zh = os.environ.get("AZURE_VOICE_ID") or DEFAULT_VOICE_ZH
    voice_en = os.environ.get("AZURE_VOICE_ID_EN")  # None = skip EN

    if not args.dry_run:
        missing = [
            k for k, v in {
                "AZURE_SPEECH_KEY": key,
                "AZURE_SPEECH_REGION": region,
            }.items() if not v
        ]
        if missing:
            print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)
    if args.lang in ("en", "all") and not voice_en:
        print("Note: AZURE_VOICE_ID_EN not set — English segments will be skipped.")

    langs = {"zh", "en"} if args.lang == "all" else {args.lang}

    if args.files:
        files = [Path(f) if Path(f).is_absolute() else REPO_DIR / f for f in args.files]
    else:
        files = sorted(
            p for p in REPO_DIR.iterdir() if re.match(r".+-day\d+\.html$", p.name)
        )

    for path in files:
        try:
            process_page(path, key, region, voice_zh, voice_en, langs, args.dry_run)
        except Exception as e:
            print(f"  PAGE FAILED: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
