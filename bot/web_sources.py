"""Render Responses URL annotations as real, bounded Discord source links."""
import re
from urllib.parse import quote, urlparse


def discord_answer(response, limit=1800):
    sources = []
    parts = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) != "output_text":
                continue
            text = content.text
            edits = {}
            for citation in getattr(content, "annotations", []) or []:
                if getattr(citation, "type", None) != "url_citation":
                    continue
                url = getattr(citation, "url", "")
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    continue
                url = quote(url, safe=":/?&=#%+~!$;,@-._")
                if url not in sources:
                    # Keep source links complete within Discord's message limit.
                    if len(url) > 300 or len(sources) >= 4:
                        continue
                    sources.append(url)
                start, end = getattr(citation, "start_index", -1), getattr(citation, "end_index", -1)
                if 0 <= start < end <= len(text):
                    edits.setdefault((start, end), f"[{sources.index(url) + 1}]")
            for (start, end), replacement in sorted(edits.items(), reverse=True):
                text = text[:start] + replacement + text[end:]
            parts.append(text)
    body = "\n".join(parts).strip() or (getattr(response, "output_text", "") or "").strip()
    # Internal search markers are not usable links in Discord.
    body = re.sub(r"\ue200[^\ue201]*\ue201", "", body).strip()
    links = "\n".join(f"[来源 {i}](<{url}>)" for i, url in enumerate(sources, 1))
    suffix = "\n\n" + links if links else ""
    budget = max(0, limit - len(suffix))
    if len(body) > budget:
        body = body[:max(0, budget - 1)].rstrip() + "…"
    return body + suffix
