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
SEGMENT_TAGS = ("h1", "h2", "h3", "p")
REPO_DIR = Path(__file__).parent.resolve()
AUDIO_DIR = REPO_DIR / "audio"


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def plain_text(attr_value: str) -> str:
    """Strip inline HTML (<br>, <strong>, &ldquo;, …) from an attribute value
    so the hash matches what the browser sees as element.textContent."""
    return BeautifulSoup(attr_value, "html.parser").get_text().strip()


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


def collect_segments(soup) -> list[tuple]:
    """Return [(element, lang, plain_text), ...] for tts-readable segments."""
    out = []
    for tag in SEGMENT_TAGS:
        for el in soup.find_all(tag):
            if el.find_parent("nav"):
                continue
            if el.find_parent(class_="mmd-controls"):
                continue
            for lang in ("zh", "en"):
                attr = f"data-{lang}"
                if not el.has_attr(attr):
                    continue
                text = plain_text(el[attr])
                if not text:
                    continue
                out.append((el, lang, text))
    return out


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
    segments = collect_segments(soup)
    print(f"  {len(segments)} candidate segments")

    changed = False
    generated = skipped_existing = skipped_lang = errors = 0

    for el, lang, text in segments:
        if lang not in langs:
            skipped_lang += 1
            continue
        voice = voice_zh if lang == "zh" else voice_en
        if not voice and not dry_run:
            skipped_lang += 1
            continue

        digest = hash_text(text)
        attr_name = f"data-tts-{lang}"
        if el.get(attr_name) != digest:
            el[attr_name] = digest
            changed = True

        mp3_path = AUDIO_DIR / lang / f"{digest}.mp3"
        if mp3_path.exists():
            skipped_existing += 1
            continue
        if dry_run:
            print(f"  [{lang}] would bake {digest}.mp3 ← {text[:50]}…")
            continue
        try:
            audio = synth(app_id, api_key, voice, text)
            mp3_path.parent.mkdir(parents=True, exist_ok=True)
            mp3_path.write_bytes(audio)
            generated += 1
            print(f"  [{lang}] {digest}.mp3 ({len(audio):,} B) ← {text[:40]}…")
        except Exception as e:
            errors += 1
            print(f"  [{lang}] FAILED {digest} ← {text[:40]}…\n         {e}", file=sys.stderr)

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
