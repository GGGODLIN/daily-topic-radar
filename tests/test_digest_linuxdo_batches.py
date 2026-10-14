"""Coverage contract for the LINUX DO batch splitter.

These tests rebuild source text from the batch files. A splitter that dropped,
reordered, or capped entries would fail here even if its own counters matched.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "digest-linuxdo-batches.py"


def _run(raw: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--raw", str(raw), "--out", str(out), *extra],
        check=False,
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def _entry(title: str, url: str, body: str) -> str:
    return f"### [{title}]({url})\n\n`linuxdo:test` · T2\n\n> {body}\n\n---\n"


def _raw(*sections: str) -> str:
    # A newline before each heading matches the daily raw file, where every
    # section heading starts on its own line.
    return "# daily\n\n" + "\n".join(sections)


def _linux(*entries: str) -> str:
    return "## LINUX DO (n items)\n\n" + "".join(entries)


def _rebuilt_linux(out: Path) -> str:
    """Join the exact slices in manifest order. That is the coverage claim.

    The markdown batch is the human view and strips trailing newlines, so it
    cannot prove a cut landed on a newline. The JSON sibling keeps the slice.
    """
    manifest = _manifest(out)
    pieces: list[str] = []
    for batch in manifest["batches"]:
        payload = json.loads(
            Path(batch["path"]).with_suffix(".json").read_text(encoding="utf-8")
        )
        by_id = {piece["id"]: piece for piece in payload["fragments"]}
        for fragment_id in batch["fragment_ids"]:
            piece = by_id[fragment_id]
            assert len(piece["text"]) == piece["end"] - piece["start"]
            pieces.append(piece["text"])
    return "".join(pieces)


def test_other_sections_survive_verbatim_and_linux_is_fully_rebuilt(tmp_path: Path):
    before = "## English Tech Media (1 items)\n\n### [keep me](https://example.com/a)\n\n> untouched body\n\n---\n"
    linux_body = _entry("甲", "https://linux.do/t/topic/1", "第一篇全文") + _entry(
        "乙", "https://linux.do/t/topic/2", "第二篇全文含 ![](https://img.example/a.png)"
    )
    after = "## 中文 / 台灣 (1 items)\n\n### [也留](https://example.com/b)\n\n> 後段\n\n---\n"
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(before, _linux(linux_body), after))

    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    meta = json.loads(result.stdout)
    assert meta["entry_count"] == 2
    assert meta["fragment_count"] == 2
    assert "第一篇全文" not in result.stdout

    other = (out / "non-linuxdo.md").read_text(encoding="utf-8")
    assert other == _raw(before, after)
    assert "LINUX DO" not in other
    # _raw joins sections with a newline, so the blank line before the next
    # heading is inside the LINUX DO span.
    assert _rebuilt_linux(out) == linux_body + "\n"

    manifest = _manifest(out)
    assert manifest["batches"][0]["topic_urls"] == [
        "https://linux.do/t/topic/1",
        "https://linux.do/t/topic/2",
    ]
    assert manifest["batches"][0]["source_lines"] == [13, 20]


def test_resurface_marker_keeps_the_topic_url(tmp_path: Path):
    url = "https://linux.do/t/topic/4"
    body = f"### 🔁 [舊題重現]({url})\n\n`linuxdo:test` · T2\n\n> 回來了\n\n---\n"
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(body)))

    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    manifest = _manifest(out)
    assert manifest["batches"][0]["topic_urls"] == [url]
    assert _rebuilt_linux(out) == body


def test_duplicate_url_is_kept_and_addressable_by_source_line(tmp_path: Path):
    url = "https://linux.do/t/topic/9"
    body = _entry("同題甲", url, "alpha-unique") + _entry("同題乙", url, "beta-unique")
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(body)))

    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    manifest = _manifest(out)
    lines = [line for batch in manifest["batches"] for line in batch["source_lines"]]
    assert lines == [5, 12]
    assert lines[0] != lines[1]
    rebuilt = _rebuilt_linux(out)
    assert rebuilt.count(url) == 2
    assert "alpha-unique" in rebuilt and "beta-unique" in rebuilt


def test_long_entry_splits_across_batches_without_losing_the_tail(tmp_path: Path):
    # One entry, no newline inside the body. Budget is below the entry length,
    # so the cut has to land mid-line and the tail must still come back.
    body = "甲" * 20 + "乙" * 20 + "丙" * 10
    entry = _entry("長文", "https://linux.do/t/topic/7", body)
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(entry)))

    result = _run(raw, out, "--max-chars", "20", "--max-items", "1")
    assert result.returncode == 0, result.stdout
    meta = json.loads(result.stdout)
    assert meta["entry_count"] == 1
    assert meta["fragment_count"] > 1
    assert meta["batch_count"] == meta["fragment_count"]

    manifest = _manifest(out)
    covered = 0
    for batch in manifest["batches"]:
        assert batch["chars"] <= 20
        assert len(batch["fragment_ids"]) == 1
        covered += batch["chars"]
    assert covered == len(entry)
    rebuilt = _rebuilt_linux(out)
    assert rebuilt == entry
    assert rebuilt.endswith("丙" * 10 + "\n\n---\n")
    # The last fragment is the tail, not a truncated prefix of the entry.
    last = manifest["batches"][-1]
    last_payload = json.loads(
        Path(last["path"]).with_suffix(".json").read_text(encoding="utf-8")
    )
    assert last_payload["fragments"][0]["text"].endswith("丙" * 10 + "\n\n---\n")


def test_batch_respects_char_budget_and_item_cap(tmp_path: Path):
    entries = "".join(
        _entry(f"t{i}", f"https://linux.do/t/topic/{i}", "x" * 30) for i in range(5)
    )
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(entries)))

    result = _run(raw, out, "--max-chars", "100", "--max-items", "2")
    assert result.returncode == 0, result.stdout
    manifest = _manifest(out)
    assert manifest["entry_count"] == 5
    for batch in manifest["batches"]:
        assert batch["chars"] <= 100
        assert len(batch["fragment_ids"]) <= 2
    assert sum(batch["chars"] for batch in manifest["batches"]) == len(entries)


def test_forty_entry_cap_is_per_batch_not_a_total(tmp_path: Path):
    entries = "".join(
        _entry(f"t{i}", f"https://linux.do/t/topic/{i}", "ok") for i in range(41)
    )
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(entries)))

    result = _run(raw, out, "--max-chars", "18000", "--max-items", "40")
    assert result.returncode == 0, result.stdout
    manifest = _manifest(out)
    assert manifest["entry_count"] == 41
    assert manifest["batch_count"] == 2
    assert [len(batch["fragment_ids"]) for batch in manifest["batches"]] == [40, 1]
    assert _rebuilt_linux(out) == entries


def test_missing_section_writes_empty_manifest_and_full_copy(tmp_path: Path):
    text = "## Reddit (1 items)\n\n### [only](https://example.com/r)\n\n> body\n\n---\n"
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, text)

    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    manifest = _manifest(out)
    assert manifest["ok"] is True
    assert manifest["entry_count"] == 0
    assert manifest["fragment_count"] == 0
    assert manifest["batch_count"] == 0
    assert manifest["batches"] == []
    assert (out / "non-linuxdo.md").read_text(encoding="utf-8") == text
    assert {path.name for path in out.iterdir()} == {"manifest.json", "non-linuxdo.md"}


def test_malformed_section_and_bad_path_exit_1(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, "## LINUX DO\n\nthis is not an entry\n")
    result = _run(raw, out)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "err" in payload and payload["err"]
    assert not out.exists()

    missing = _run(tmp_path / "nope.md", out)
    assert missing.returncode == 1
    assert "not found" in json.loads(missing.stdout)["err"]


def test_refresh_rewrites_same_names_and_keeps_unrelated_files(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "one"))))
    first = _run(raw, out)
    assert first.returncode == 0, first.stdout
    note = out / "scratch.txt"
    _write(note, "leave me")

    _write(raw, _raw(_linux(_entry("乙", "https://linux.do/t/topic/2", "two"))))
    second = _run(raw, out)
    assert second.returncode == 0, second.stdout
    assert note.read_text(encoding="utf-8") == "leave me"
    rebuilt = _rebuilt_linux(out)
    assert "two" in rebuilt
    assert "one" not in rebuilt


def test_content_limits_do_not_claim_the_thread_was_read(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "正文"))))
    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    limits = _manifest(out)["content_limits"]
    assert any("Replies are absent" in note for note in limits)
    assert all("full thread" not in note.lower() for note in limits)


def _decode_workflow(stdout: str) -> dict:
    wrapper = json.loads(stdout)
    assert set(wrapper) == {"version", "payload"}
    assert wrapper["version"] == 2
    return json.loads(base64.b64decode(wrapper["payload"]).decode("utf-8"))


def _expand(compact: dict) -> list[dict]:
    """Rebuild the per-batch identity fields the compact form left out.

    This is the contract the workflow side depends on. It must agree with the
    manifest on disk; a shortcut here that read the manifest back would hide a
    generator that dropped a batch.
    """
    urls = [
        f"https://linux.do/t/topic/{topic_id}" if topic_id is not None else None
        for topic_id in compact["topic_ids"]
    ]
    for index, url in compact["url_overrides"]:
        urls[index] = url
    part_counts = [1] * compact["entry_count"]
    for index, count in compact["split_entries"]:
        part_counts[index] = count

    stream = []
    for entry_index, parts in enumerate(part_counts):
        for part in range(parts):
            stream.append(
                {
                    "id": f"e{entry_index + 1:04d}-f{part:03d}",
                    "url": urls[entry_index],
                }
            )
    assert len(stream) == compact["fragment_count"]

    expanded = []
    cursor = 0
    for batch_index, (chars, count) in enumerate(compact["batch_sizes"]):
        taken = stream[cursor : cursor + count]
        cursor += count
        seen: list[str] = []
        for piece in taken:
            if piece["url"] and piece["url"] not in seen:
                seen.append(piece["url"])
        expanded.append(
            {
                "id": f"batch-{batch_index + 1:03d}",
                "chars": chars,
                "fragment_ids": [piece["id"] for piece in taken],
                "topic_urls": seen,
            }
        )
    assert cursor == len(stream)
    return expanded


def test_workflow_output_expands_to_the_manifest(tmp_path: Path):
    canonical = "https://linux.do/t/topic/15"
    odd = "https://example.com/not-a-topic"
    # The long entry splits, so the compact form has to name it in
    # split_entries or the expanded fragment ids stop at the first part.
    body = (
        _entry("一般", canonical, "短文")
        + _entry("怪網址", odd, "乙")
        + _entry("空網址", "", "丙")
        + _entry("很長", "https://linux.do/t/topic/99", "甲" * 30)
        + _entry("前導零", "https://linux.do/t/topic/0009", "丁")
        + _entry("大數", "https://linux.do/t/topic/9007199254740993", "戊")
    )
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(body)))

    result = _run(raw, out, "--max-chars", "40", "--workflow-output")
    assert result.returncode == 0, result.stderr
    compact = _decode_workflow(result.stdout)
    assert compact["topic_ids"] == [15, None, None, 99, None, None]
    assert compact["url_overrides"] == [[1, odd], [2, ""], [4, "https://linux.do/t/topic/0009"], [5, "https://linux.do/t/topic/9007199254740993"]]
    assert compact["split_entries"]
    on_disk = _manifest(out)
    for expanded, batch in zip(_expand(compact), on_disk["batches"], strict=True):
        assert expanded["id"] == batch["id"]
        assert expanded["chars"] == batch["chars"]
        assert expanded["fragment_ids"] == batch["fragment_ids"]
        assert expanded["topic_urls"] == batch["topic_urls"]


def test_workflow_output_carries_every_batch_id(tmp_path: Path):
    # 41 entries at one per batch. A transport that summarized and let the
    # receiver rebuild the list could drop one and still have matching counts.
    entries = "".join(
        _entry(f"t{i}", f"https://linux.do/t/topic/{i}", "x" * 10) for i in range(41)
    )
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(entries)))

    result = _run(raw, out, "--max-items", "1", "--workflow-output")
    assert result.returncode == 0, result.stderr
    compact = _decode_workflow(result.stdout)
    expanded = _expand(compact)
    assert [batch["id"] for batch in expanded] == [f"batch-{i:03d}" for i in range(1, 42)]
    on_disk = _manifest(out)
    for got, batch in zip(expanded, on_disk["batches"], strict=True):
        assert got["fragment_ids"] == batch["fragment_ids"]
        assert got["topic_urls"] == batch["topic_urls"]
        assert got["chars"] == batch["chars"]


def test_default_stdout_stays_the_summary(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "正文"))))
    result = _run(raw, out)
    assert result.returncode == 0, result.stdout
    meta = json.loads(result.stdout)
    assert "payload" not in meta
    assert "batch_ids" in meta
    assert "topic_urls" not in meta


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_non_positive_budget_is_an_error(tmp_path: Path, bad: str):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "正文"))))
    result = _run(raw, out, "--max-chars", bad)
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False


_READER_TEMPLATE = """\
export const meta = { name: 'reader' }
const prepared = null
const NOTE = 'const prepared = null is data, not the marker'
function run() {
  return prepared
}
"""


def _prepared_literal(script_text: str) -> dict:
    """Take the JSON object off the substituted line. The template is not executed."""
    line = next(
        row for row in script_text.splitlines() if row.startswith("const prepared = {")
    )
    assert line.endswith(";")
    return json.loads(line.removeprefix("const prepared = ").removesuffix(";"))


def test_workflow_script_embeds_disk_manifest_without_shipping_it(tmp_path: Path):
    # Topic numbers that failed when the transport rebuilt them: a 3020144-class
    # id, a leading zero, an integer past 2^53-1, and a long entry cut into parts.
    # The other section must stay outside the embedded batches.
    odd = "https://linux.do/t/topic/3020144"
    leading = "https://linux.do/t/topic/03020140"
    huge = "https://linux.do/t/topic/9007199254740993"
    long_body = "甲" * 30
    body = (
        _entry("失敗例", odd, "甲文")
        + _entry("前導零", leading, "乙文")
        + _entry("大數", huge, "丙文")
        + _entry("很長", "https://linux.do/t/topic/3020140", long_body)
    )
    before = "## English Tech Media (1 items)\n\n### [keep](https://example.com/a)\n\n> stay\n\n---\n"
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    template = tmp_path / "reader-template.js"
    script = tmp_path / "reader.js"
    _write(raw, _raw(before, _linux(body)))
    _write(template, _READER_TEMPLATE)

    result = _run(
        raw,
        out,
        "--max-chars",
        "40",
        "--workflow-script",
        str(script),
        "--reader-template",
        str(template),
    )
    assert result.returncode == 0, result.stderr
    meta = json.loads(result.stdout)
    assert meta["workflow_script"] == str(script)
    assert "batches" not in meta
    assert "3020144" not in result.stdout
    assert "9007199254740993" not in result.stdout
    assert template.read_text(encoding="utf-8") == _READER_TEMPLATE

    written = script.read_text(encoding="utf-8")
    assert sum(line == "const prepared = null" for line in written.splitlines()) == 0
    assert sum(line.startswith("const prepared = {") for line in written.splitlines()) == 1
    assert "const NOTE = 'const prepared = null is data, not the marker'" in written
    assert written.endswith("  return prepared\n}\n")

    embedded = _prepared_literal(written)
    on_disk = _manifest(out)
    assert embedded == on_disk
    urls = [url for batch in embedded["batches"] for url in batch["topic_urls"]]
    assert odd in urls
    assert leading in urls
    assert huge in urls
    assert "https://linux.do/t/topic/3020140" in urls
    assert "https://example.com/a" not in json.dumps(embedded["batches"])
    assert any(batch["chars"] > 0 and len(batch["fragment_ids"]) >= 1 for batch in embedded["batches"])
    assert embedded["fragment_count"] > embedded["entry_count"]
    rebuilt = _rebuilt_linux(out)
    assert long_body in rebuilt
    assert rebuilt.count("https://linux.do/t/topic/3020140)") == 1


def test_workflow_script_keeps_line_separator_inside_the_literal(tmp_path: Path):
    # source_path is copied onto the manifest verbatim. A separator in a title
    # or a link target is parsed away before the manifest exists.
    raw = tmp_path / ("raw" + " " + "file.md")
    out = tmp_path / "out"
    template = tmp_path / "reader-template.js"
    script = tmp_path / "reader.js"
    _write(raw, _raw(_linux(_entry("分隔", "https://linux.do/t/topic/3020144", "正文"))))
    _write(template, _READER_TEMPLATE)

    result = _run(
        raw, out, "--workflow-script", str(script), "--reader-template", str(template)
    )
    assert result.returncode == 0, result.stderr
    written = script.read_text(encoding="utf-8")
    assert " " not in written
    assert "\\u2028" in written
    assert sum(line.startswith("const prepared = {") for line in written.splitlines()) == 1
    embedded = _prepared_literal(written)
    assert embedded == _manifest(out)
    assert " " in embedded["source_path"]


def test_workflow_script_rejects_marker_count_and_half_a_pair(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    template = tmp_path / "reader-template.js"
    script = tmp_path / "reader.js"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "正文"))))

    _write(template, "const ready = null\n")
    missing = _run(
        raw, out, "--workflow-script", str(script), "--reader-template", str(template)
    )
    assert missing.returncode != 0
    assert json.loads(missing.stdout)["ok"] is False
    assert not script.exists()

    _write(template, "const prepared = null\nconst prepared = null\n")
    doubled = _run(
        raw, out, "--workflow-script", str(script), "--reader-template", str(template)
    )
    assert doubled.returncode != 0
    assert json.loads(doubled.stdout)["ok"] is False
    assert not script.exists()

    only_script = _run(raw, out, "--workflow-script", str(script))
    assert only_script.returncode != 0
    assert json.loads(only_script.stdout)["ok"] is False
    assert not script.exists()

    only_template = _run(raw, out, "--reader-template", str(template))
    assert only_template.returncode != 0
    assert json.loads(only_template.stdout)["ok"] is False
    assert not script.exists()


def test_workflow_script_refuses_a_symlink_destination(tmp_path: Path):
    raw = tmp_path / "raw.md"
    out = tmp_path / "out"
    template = tmp_path / "reader-template.js"
    elsewhere = tmp_path / "elsewhere.js"
    link = tmp_path / "reader.js"
    _write(raw, _raw(_linux(_entry("甲", "https://linux.do/t/topic/1", "正文"))))
    _write(template, _READER_TEMPLATE)
    _write(elsewhere, "original\n")
    link.symlink_to(elsewhere)

    result = _run(
        raw,
        out,
        "--workflow-script",
        str(link),
        "--reader-template",
        str(template),
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["ok"] is False
    assert elsewhere.read_text(encoding="utf-8") == "original\n"
    assert link.is_symlink()
