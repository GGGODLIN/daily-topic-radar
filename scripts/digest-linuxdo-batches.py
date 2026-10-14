#!/usr/bin/env python3
"""Split a daily raw markdown's LINUX DO section into full-coverage batches.

The helper keeps every character of every LINUX DO entry. It does not rank,
filter, or drop topics. A single entry longer than one batch budget is cut
into consecutive fragments; joining those fragments rebuilds the entry text.

RSS excerpts are the original post only. Images are markdown URLs, and reply
threads are not in the raw file. Nothing here claims the forum thread was read.

Raw markdown is untrusted data. It is parsed and copied into output files, never
executed and never interpolated into a shell command.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

# A topic url that matches this carries no information beyond its number, so
# the compact payload stores the number and the reader puts the prefix back.
_CANONICAL_TOPIC = re.compile(r"^https://linux\.do/t/topic/(\d+)$")

DEFAULT_MAX_CHARS = 18000
DEFAULT_MAX_ITEMS = 40
SECTION_TITLE = "LINUX DO"
# The reader template carries this one line. Replacing any other occurrence
# would splice the manifest into a string or a comment the workflow still runs.
PREPARED_MARKER = "const prepared = null"
NOTE = (
    "RSS excerpt is the original post only. Images are URLs with no image "
    "bytes. Replies are absent. This helper does not claim the forum thread "
    "was read."
)


class BatchError(Exception):
    """Caller-facing failure. The message is the whole explanation."""


def _section_span(text: str) -> tuple[int, int] | None:
    """Span of the LINUX DO body, heading excluded, ending at the next section.

    A heading that merely contains the words is not a match: the line has to
    start with the marker, so a mention inside an entry stays put.
    """
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.startswith(f"## {SECTION_TITLE}\n") or line.startswith(
            f"## {SECTION_TITLE} "
        ):
            # splitlines(keepends) keeps one trailing newline, but the
            # daily file leaves a blank line before the first entry.
            body_start = offset + len(line) + 1
            rest = text[body_start:]
            body_end = len(text)
            cursor = 0
            for later in rest.splitlines(keepends=True):
                if later.startswith("## ") and not later.startswith("###"):
                    # End on the line boundary. Subtracting one would eat the
                    # newline that the next section's heading is written with.
                    body_end = body_start + cursor
                    break
                cursor += len(later)
            return body_start, body_end
        offset += len(line)
    return None


def _entry_blocks(section: str, section_line: int) -> list[dict]:
    """Split the section into ### blocks.

    A repeated URL is a separate entry. source_line is the 1-based line of the
    heading in the raw file, so a duplicate can be told apart from its twin.
    """
    entries: list[dict] = []
    offset = 0
    line_no = section_line
    current: dict | None = None
    for line in section.splitlines(keepends=True):
        if line.startswith("### "):
            if current is not None:
                current["text"] = section[current["start"] : offset]
                entries.append(current)
            title, url = _heading_identity(line)
            current = {
                "title": title,
                "url": url,
                "identity": url or title,
                "source_line": line_no,
                "start": offset,
            }
        if current is None and line.strip():
            raise BatchError(
                f"LINUX DO section has text before the first ### entry "
                f"at source line {line_no}"
            )
        offset += len(line)
        line_no += line.count("\n")
    if current is not None:
        current["text"] = section[current["start"] : offset]
        entries.append(current)
    if not entries and section.strip():
        raise BatchError("LINUX DO section has text but no ### entries")
    return entries


def _heading_identity(line: str) -> tuple[str, str]:
    # render_item prefixes a resurfaced item with "🔁 ". The marker is not
    # part of the title; leaving it on makes the link unparseable and the
    # topic url comes out empty.
    body = line[4:].strip().removeprefix("🔁 ").strip()
    if body.startswith("[") and "](" in body and body.endswith(")"):
        title, _, rest = body[1:].partition("](")
        return title, rest[:-1]
    return body, ""


def _fragments(entries: list[dict], max_chars: int) -> list[dict]:
    pieces: list[dict] = []
    for index, entry in enumerate(entries):
        text = entry["text"]
        if text == "":
            raise BatchError(
                f"empty LINUX DO entry at source line {entry['source_line']}"
            )
        entry_id = f"e{index + 1:04d}"
        spans = _cut_spans(text, max_chars)
        for part, (start, end) in enumerate(spans):
            pieces.append(
                {
                    "id": f"{entry_id}-f{part:03d}",
                    "entry_id": entry_id,
                    "topic_url": entry["url"],
                    "title": entry["title"],
                    "source_line": entry["source_line"],
                    "part": part,
                    "part_count": len(spans),
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                }
            )
    return pieces


def _cut_spans(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Prefer a newline inside the budget.

    The markdown view re-wraps a fragment as its own block, so a mid-line cut
    would come back with a newline the source never had. A line longer than
    the budget has nowhere to break; the JSON slice keeps that exact text.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        window = text[start:hard_end]
        break_at = window.rfind("\n")
        end = hard_end if break_at <= 0 else start + break_at + 1
        spans.append((start, end))
        start = end
    return spans


def _batches(pieces: list[dict], max_chars: int, max_items: int) -> list[dict]:
    """Pack whole fragments. A fragment is never split across batches.

    chars counts fragment text only. Heading and note lines are overhead, so
    the markdown file is longer than the manifest's chars.
    """
    batches: list[dict] = []
    current: list[dict] = []
    used = 0
    for piece in pieces:
        size = len(piece["text"])
        if size > max_chars:
            raise BatchError(
                f"fragment {piece['id']} is {size} chars, over max_chars {max_chars}"
            )
        if current and (used + size > max_chars or len(current) >= max_items):
            batches.append({"fragments": current, "chars": used})
            current = []
            used = 0
        current.append(piece)
        used += size
    if current:
        batches.append({"fragments": current, "chars": used})
    return batches


def _batch_markdown(batch_id: str, fragments: list[dict]) -> str:
    lines = [
        f"# {batch_id}",
        "",
        f"> {NOTE}",
        "",
    ]
    for piece in fragments:
        span = f"chars [{piece['start']}:{piece['end']}]"
        where = piece["topic_url"] or piece["title"]
        lines.append(
            f"## {piece['id']} · {where} · source line {piece['source_line']} · {span}"
        )
        lines.append("")
        lines.append(piece["text"].rstrip("\n"))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _batch_payload(batch_id: str, fragments: list[dict]) -> dict:
    """Exact slices. The markdown view strips trailing newlines for reading."""
    return {
        "id": batch_id,
        "fragments": [
            {
                "id": piece["id"],
                "entry_id": piece["entry_id"],
                "topic_url": piece["topic_url"],
                "source_line": piece["source_line"],
                "start": piece["start"],
                "end": piece["end"],
                "text": piece["text"],
            }
            for piece in fragments
        ],
    }


def _non_linuxdo(text: str, span: tuple[int, int] | None) -> str:
    if span is None:
        return text
    start, end = span
    # Drop the heading line too, not only the body span.
    heading_start = text.rfind("\n## ", 0, start)
    heading_start = 0 if heading_start < 0 else heading_start + 1
    return text[:heading_start] + text[end:]


def build(raw_text: str, source_path: str, max_chars: int, max_items: int) -> dict:
    if max_chars < 1 or max_items < 1:
        raise BatchError("max_chars and max_items must be >= 1")
    span = _section_span(raw_text)
    if span is None:
        return {
            "entries": [],
            "fragments": [],
            "batches": [],
            "non_linuxdo": raw_text,
            "limits": [],
            "source_path": source_path,
            "max_chars": max_chars,
            "max_items": max_items,
        }
    section = raw_text[span[0] : span[1]]
    section_line = raw_text[: span[0]].count("\n") + 1
    entries = _entry_blocks(section, section_line)
    pieces = _fragments(entries, max_chars)
    grouped = _batches(pieces, max_chars, max_items)
    limits = _limit_notes(entries, pieces, max_chars)
    return {
        "entries": entries,
        "fragments": pieces,
        "batches": grouped,
        "non_linuxdo": _non_linuxdo(raw_text, span),
        "limits": limits,
        "source_path": source_path,
        "max_chars": max_chars,
        "max_items": max_items,
    }


def _limit_notes(entries: list[dict], pieces: list[dict], max_chars: int) -> list[str]:
    notes = [NOTE]
    split = [entry for entry in entries if len(entry["text"]) > max_chars]
    if split:
        notes.append(
            f"{len(split)} entries were longer than {max_chars} chars and were "
            "split into consecutive fragments; no characters were dropped"
        )
    if len(pieces) != len(entries):
        notes.append(
            "fragment_count is above entry_count because long entries were split"
        )
    return notes


def write_out(plan: dict, out_dir: Path) -> dict:
    """Write manifest, non-linuxdo copy, and one markdown file per batch.

    A repeat run refreshes these names. Files this helper did not create stay.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    non_path = out_dir / "non-linuxdo.md"
    _write_same(non_path, plan["non_linuxdo"])

    manifest_batches = []
    for index, batch in enumerate(plan["batches"]):
        batch_id = f"batch-{index + 1:03d}"
        path = out_dir / f"{batch_id}.md"
        _write_same(path, _batch_markdown(batch_id, batch["fragments"]))
        _write_same(
            out_dir / f"{batch_id}.json",
            json.dumps(
                _batch_payload(batch_id, batch["fragments"]),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        manifest_batches.append(
            {
                "id": batch_id,
                "path": str(path),
                "chars": batch["chars"],
                "fragment_ids": [piece["id"] for piece in batch["fragments"]],
                "topic_urls": _topic_urls(batch["fragments"]),
                "source_lines": [piece["source_line"] for piece in batch["fragments"]],
            }
        )

    manifest = {
        "ok": True,
        "source_path": plan["source_path"],
        "non_linuxdo_path": str(non_path),
        "entry_count": len(plan["entries"]),
        "fragment_count": len(plan["fragments"]),
        "batch_count": len(manifest_batches),
        "max_chars": plan["max_chars"],
        "max_items": plan["max_items"],
        "batches": manifest_batches,
        "content_limits": plan["limits"],
    }
    _write_same(
        out_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def _topic_urls(fragments: list[dict]) -> list[str]:
    seen: list[str] = []
    for piece in fragments:
        url = piece["topic_url"]
        if url and url not in seen:
            seen.append(url)
    return seen


def _write_same(path: Path, content: str) -> None:
    # A symlink here would write somewhere the caller did not name.
    if path.is_symlink():
        raise BatchError(f"refusing to write through symlink {path}")
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _fragments_in_order(manifest: dict, out_dir: Path) -> list[dict]:
    """Read the slices back in manifest order.

    The compact payload is derived from the bytes just written, not from the
    plan still in memory, so a batch file that did not land cannot be described
    as if it had.
    """
    ordered: list[dict] = []
    for batch in manifest["batches"]:
        payload = json.loads((out_dir / f"{batch['id']}.json").read_text(encoding="utf-8"))
        by_id = {piece["id"]: piece for piece in payload["fragments"]}
        for fragment_id in batch["fragment_ids"]:
            ordered.append(by_id[fragment_id])
    return ordered


def _compact(manifest: dict, fragments: list[dict]) -> dict:
    """Drop what the reader can rebuild, keep what it cannot.

    Fragment ids are `e{entry:04d}-f{part:03d}` and batch files are
    `batch-{n:03d}.md` under the out dir, so neither string is sent. A
    canonical topic url is just its number. Anything else — a different host,
    an empty url — goes in url_overrides with the exact text, because guessing
    it back would lose the entry.
    """
    topic_ids: list[int | None] = []
    overrides: list[list] = []
    # Part 0 of each entry, in order. Later parts of the same entry follow it
    # because fragments are cut consecutively and never reordered.
    heads = [piece for piece in fragments if piece["id"].endswith("-f000")]
    for index, head in enumerate(heads):
        match = _CANONICAL_TOPIC.fullmatch(head["topic_url"])
        if match and str(int(match.group(1))) == match.group(1) and int(match.group(1)) <= 9007199254740991:
            topic_ids.append(int(match.group(1)))
        else:
            topic_ids.append(None)
            overrides.append([index, head["topic_url"]])
    splits = []
    for index, head in enumerate(heads):
        at = fragments.index(head)
        after = fragments[at + 1 :]
        nxt = next(
            (offset for offset, piece in enumerate(after) if piece["id"].endswith("-f000")),
            len(after),
        )
        if nxt + 1 > 1:
            splits.append([index, nxt + 1])
    return {
        "ok": manifest["ok"],
        "source_path": manifest["source_path"],
        "entry_count": manifest["entry_count"],
        "fragment_count": manifest["fragment_count"],
        "max_chars": manifest["max_chars"],
        "max_items": manifest["max_items"],
        "batch_sizes": [
            [batch["chars"], len(batch["fragment_ids"])] for batch in manifest["batches"]
        ],
        "topic_ids": topic_ids,
        "url_overrides": overrides,
        "split_entries": splits,
        "content_limits": manifest["content_limits"],
    }


def _workflow_output(manifest: dict, fragments: list[dict]) -> str:
    raw = json.dumps(_compact(manifest, fragments), ensure_ascii=False).encode("utf-8")
    return json.dumps(
        {"version": 2, "payload": base64.b64encode(raw).decode("ascii")},
        ensure_ascii=False,
    )


def _manifest_literal(manifest: dict) -> str:
    """JSON literal for a JS source line.

    ensure_ascii keeps every URL byte in the ASCII range, so a quote or
    slash inside an untrusted title cannot close the literal. U+2028 and
    U+2029 are not escaped by ensure_ascii, and a raw one would end the JS
    line before the manifest object closed.
    """
    literal = json.dumps(manifest, ensure_ascii=True, separators=(",", ":"))
    return literal.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _render_workflow_script(template: str, manifest: dict) -> str:
    # A whole line, not a substring. The same characters inside a string or
    # comment are data the workflow still carries, not a second marker.
    marker_line = f"{PREPARED_MARKER}\n"
    count = template.count(marker_line)
    if count != 1:
        raise BatchError(
            f"reader template must contain {PREPARED_MARKER!r} exactly once, found {count}"
        )
    rendered = template.replace(
        marker_line, f"const prepared = {_manifest_literal(manifest)};\n", 1
    )
    if rendered.count("const prepared = {") != 1 or PREPARED_MARKER in rendered.splitlines():
        raise BatchError("rendered workflow script does not keep a single prepared binding")
    return rendered


def _stdout_meta(manifest: dict) -> dict:
    return {
        "ok": manifest["ok"],
        "source_path": manifest["source_path"],
        "non_linuxdo_path": manifest["non_linuxdo_path"],
        "entry_count": manifest["entry_count"],
        "fragment_count": manifest["fragment_count"],
        "batch_count": manifest["batch_count"],
        "max_chars": manifest["max_chars"],
        "max_items": manifest["max_items"],
        "batch_ids": [batch["id"] for batch in manifest["batches"]],
        "content_limits": manifest["content_limits"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument(
        "--workflow-output",
        action="store_true",
        help="print a compact base64 manifest instead of the summary",
    )
    parser.add_argument("--workflow-script", type=Path)
    parser.add_argument("--reader-template", type=Path)
    args = parser.parse_args(argv)
    if (args.workflow_script is None) != (args.reader_template is None):
        print(json.dumps(
            {"ok": False, "err": "--workflow-script and --reader-template must be given together"},
            ensure_ascii=False,
        ))
        return 1
    try:
        if not args.raw.is_file():
            raise BatchError(f"raw file not found: {args.raw}")
        raw_text = args.raw.read_text(encoding="utf-8")
        plan = build(raw_text, str(args.raw), args.max_chars, args.max_items)
        manifest = write_out(plan, args.out)
        if args.workflow_script is not None:
            # Disk is the authority. The plan still in memory is not re-read.
            on_disk = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
            template = args.reader_template.read_text(encoding="utf-8")
            _write_same(args.workflow_script, _render_workflow_script(template, on_disk))
    except BatchError as exc:
        print(json.dumps({"ok": False, "err": str(exc)}, ensure_ascii=False))
        return 1
    except OSError as exc:
        print(json.dumps({"ok": False, "err": f"cannot write output: {exc}"}, ensure_ascii=False))
        return 1
    if args.workflow_output:
        print(_workflow_output(manifest, _fragments_in_order(manifest, args.out)))
    else:
        meta = _stdout_meta(manifest)
        if args.workflow_script is not None:
            meta["workflow_script"] = str(args.workflow_script)
        print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
