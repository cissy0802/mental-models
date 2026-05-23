#!/usr/bin/env python3
"""Apply i18n to mental-models-daily pages.

Adds data-zh / data-en attributes to translatable elements, marks
<html data-i18n-mode="full">, flags redundant "English Summary"
sections with data-hide-in="en", and (idempotently) injects the
i18n-tts.js script tag.

Translations are batched per page via the Claude API.

Usage:
    pip install beautifulsoup4 anthropic
    export ANTHROPIC_API_KEY=...
    python3 apply-i18n.py                              # all *-dayNN.html pages
    python3 apply-i18n.py cognitive-biases-day02.html  # specific files
    python3 apply-i18n.py --force <file>               # re-translate already-marked
    python3 apply-i18n.py --dry-run                    # show what would change
"""

import argparse
import json
import os
import re
import sys

from anthropic import Anthropic
from bs4 import BeautifulSoup

MODEL = "claude-opus-4-7"
CHINESE_RE = re.compile(r"[一-鿿]")


def has_chinese(s: str) -> bool:
    return bool(CHINESE_RE.search(s))


def inner_html(el) -> str:
    return el.decode_contents().strip()


def collect_translatable(soup):
    """Return [(element, source_lang, source_html), ...] needing translation."""
    out = []
    seen = set()

    def add(el):
        if id(el) in seen:
            return
        seen.add(id(el))
        if el.has_attr("data-zh") and el.has_attr("data-en"):
            return
        html = inner_html(el)
        if not html:
            return
        lang = "zh" if has_chinese(html) else "en"
        out.append((el, lang, html))

    # UI / metadata elements
    for sel in [
        "title",
        "header .category",
        "header h1",
        "header .date",
        ".card-header h2",
        ".card-header .en",
        ".section-label",
        ".example .label",
        ".prompts .lang",
        "nav a",
    ]:
        for el in soup.select(sel):
            add(el)

    # Body paragraphs — exclude prompt content and English-Summary content,
    # which stay in their native language (handled by hide-in logic instead)
    for p in soup.select(".section p, .example p"):
        if id(p) in seen:
            continue
        seen.add(id(p))
        if p.has_attr("data-zh") and p.has_attr("data-en"):
            continue
        label_text = ""
        sec = p.find_parent(class_="section")
        if sec:
            lab = sec.find(class_="section-label")
            if lab:
                label_text = lab.get_text()
        if "English Summary" in label_text:
            continue
        if "AI Prompt" in label_text or "提示词" in label_text:
            # Prompt items handled separately
            continue
        html = inner_html(p)
        if not html:
            continue
        lang = "zh" if has_chinese(html) else "en"
        out.append((p, lang, html))

    return out


def translate_batch(client: Anthropic, items: list[tuple[str, str]]) -> list[str]:
    if not items:
        return []
    lines = []
    for i, (lang, html) in enumerate(items):
        target = "English" if lang == "zh" else "Chinese"
        lines.append(f"--- ITEM {i} (translate to {target}) ---\n{html}")

    prompt = f"""You are translating strings for a bilingual web page about mental models for a thoughtful, technical audience (the user "BigCat" is interested in AI, neuroscience, philosophy, investing, leadership).

Rules:
- Preserve ALL inline HTML tags exactly as-is: <strong>, <em>, <br>, <br/>, etc.
- Preserve HTML entities: &ldquo; &rdquo; &mdash; &ndash; &nbsp; &rarr; &amp; etc.
- Preserve numbered markers (①②③), arrows (→), em-dashes (——), and punctuation style appropriate to the target language.
- Match the original tone: confident, concise, insight-dense. NOT word-for-word literal — produce natural fluent prose in the target language.
- For ZH→EN: use curly quotes (&ldquo; &rdquo;) and em-dashes (&mdash;); avoid stiff Chinglish.
- For EN→ZH: use Chinese full-width punctuation（），""——; preserve technical English terms when natural.
- Do NOT wrap output in markdown code blocks.
- Output JSON only: a single object {{"translations": ["...", "...", ...]}} with one string per ITEM, in the original order. Each string is the translated inner HTML.

Items to translate:

{chr(10).join(lines)}
"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    translations = data["translations"]
    if len(translations) != len(items):
        raise ValueError(
            f"Translation count mismatch: expected {len(items)}, got {len(translations)}"
        )
    return translations


def ensure_script_tag(soup, html_text: str) -> bool:
    """Inject <script src='i18n-tts.js' defer> if absent. Returns True if added."""
    if "i18n-tts.js" in html_text:
        return False
    body = soup.body
    if body is None:
        return False
    tag = soup.new_tag("script", src="i18n-tts.js")
    tag["defer"] = ""
    body.append(tag)
    return True


def process_page(client: Anthropic, path: str, force: bool, dry_run: bool) -> None:
    name = os.path.basename(path)
    print(f"\n=== {name} ===")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")

    already_full = soup.html is not None and soup.html.get("data-i18n-mode") == "full"
    if already_full and not force:
        # Only inject script tag if missing
        if "i18n-tts.js" not in html and not dry_run:
            ensure_script_tag(soup, html)
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(soup))
            print("  already full mode; added script tag")
        else:
            print("  already full mode; skipping (use --force to re-translate)")
        return

    items = collect_translatable(soup)
    if not items:
        print("  no translatable segments found")
    else:
        print(f"  found {len(items)} segments to translate")
        if dry_run:
            for el, lang, src in items[:3]:
                print(f"    [{lang}] {src[:80]}…")
            if len(items) > 3:
                print(f"    … and {len(items) - 3} more")
            return
        translations = translate_batch(client, [(lang, src) for (_, lang, src) in items])
        for (el, lang, src), tr in zip(items, translations):
            if lang == "zh":
                el["data-zh"] = src
                el["data-en"] = tr
            else:
                el["data-en"] = src
                el["data-zh"] = tr

    # Mark <html data-i18n-mode="full">
    if soup.html is not None:
        soup.html["data-i18n-mode"] = "full"

    # Hide "English Summary" sections in EN mode (content is now in main section)
    for sec in soup.select(".section"):
        lab = sec.find(class_="section-label")
        if lab and "English Summary" in lab.get_text():
            sec["data-hide-in"] = "en"

    ensure_script_tag(soup, html)

    if dry_run:
        print("  (dry-run) would write")
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(str(soup))
    print(f"  wrote {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="HTML files to process (default: all *-dayNN.html)")
    parser.add_argument("--force", action="store_true", help="Re-translate pages already in full mode")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without calling the API")
    args = parser.parse_args()

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    if args.files:
        files = args.files
    else:
        files = sorted(
            f for f in os.listdir(repo_dir) if re.match(r".+-day\d+\.html$", f)
        )

    client = None
    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
            sys.exit(1)
        client = Anthropic(api_key=api_key)

    for fname in files:
        path = fname if os.path.isabs(fname) else os.path.join(repo_dir, fname)
        try:
            process_page(client, path, args.force, args.dry_run)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
