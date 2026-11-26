"""Tests for the ``llms.txt`` docs generator.

The module under test lives in ``python/docs/src/_extension/llms_txt.py`` and is
loaded directly so the tests do not require a Sphinx installation.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "_extension" / "llms_txt.py"
DOCS_DIR = MODULE_PATH.parents[2]
SRC_DIR = DOCS_DIR / "src"

_spec = importlib.util.spec_from_file_location("llms_txt", MODULE_PATH)
assert _spec and _spec.loader
llms_txt = importlib.util.module_from_spec(_spec)
sys.modules["llms_txt"] = llms_txt
_spec.loader.exec_module(llms_txt)

LINK_LINE_RE = re.compile(r"^- \[(?P<title>.+?)\]\((?P<target>[^)]+)\)$")
ANY_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def _make_source_tree(root: Path) -> Path:
    """Create a tiny user-guide tree for link rewriting tests."""
    src = root / "src"
    page_dir = src / "user-guide" / "a"
    page_dir.mkdir(parents=True)
    (page_dir / "nb.ipynb").write_text('{"cells": []}', encoding="utf-8")
    (page_dir / "pic.png").write_bytes(b"png")
    (page_dir / "page.md").write_text(
        "\n".join(
            [
                "# Page",
                "",
                "[notebook](nb.ipynb#sec)",
                "[bare](nb)",
                "[sibling](index.md)",
                "[parent](../b/index.md)",
                "[external](https://example.com/x.md)",
                "[site](https://microsoft.github.io/autogen/dev/user-guide/a/nb.html#sec)",
                "[stable-site](https://microsoft.github.io/autogen/stable/user-guide/b/index.html)",
                "[site-root](https://microsoft.github.io/autogen/dev/user-guide/a/missing.html)",
                "[archive](https://microsoft.github.io/autogen/0.2/docs/topics/x.html)",
                "[anchor](#local)",
                "[missing](does-not-exist.md)",
                "![image](pic.png)",
                "",
                "```python",
                "[inside](nb.ipynb)",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (src / "user-guide" / "b").mkdir()
    (src / "user-guide" / "b" / "index.md").write_text("# B\n", encoding="utf-8")
    (page_dir / "index.md").write_text("# A\n", encoding="utf-8")
    return src


def _target_for_doc(docname: str) -> str:
    if docname.startswith("user-guide/"):
        return docname + ".md"
    return docname + ".html"


def test_render_notebook() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "Body"]},
            {"cell_type": "code", "source": "print('hi')"},
            {"cell_type": "markdown", "source": "   "},
        ]
    }
    rendered = llms_txt.render_notebook(nb)
    assert rendered == "# Title\nBody\n\n```python\nprint('hi')\n```"


def test_docname_for_target(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    page_rel = "user-guide/a/page.md"

    assert llms_txt.docname_for_target("nb.ipynb", page_rel, src) == "user-guide/a/nb"
    assert llms_txt.docname_for_target("nb", page_rel, src) == "user-guide/a/nb"
    assert llms_txt.docname_for_target("../b/index.md", page_rel, src) == "user-guide/b/index"
    assert llms_txt.docname_for_target("../b", page_rel, src) == "user-guide/b/index"
    assert llms_txt.docname_for_target("pic.png", page_rel, src) is None
    assert llms_txt.docname_for_target("does-not-exist.md", page_rel, src) is None
    assert llms_txt.docname_for_target("../../outside.md", page_rel, src) is None


def test_rewrite_markdown_links(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    page_rel = "user-guide/a/page.md"
    text = (src / page_rel).read_text(encoding="utf-8")
    rewritten = llms_txt.rewrite_markdown_links(text, page_rel, src, _target_for_doc)

    assert "[notebook](nb.md#sec)" in rewritten
    assert "[bare](nb.md)" in rewritten
    assert "[sibling](index.md)" in rewritten
    assert "[parent](../b/index.md)" in rewritten
    assert "[external](https://example.com/x.md)" in rewritten
    # Links to the published site point at the compiled Markdown pages.
    assert "[site](nb.md#sec)" in rewritten
    assert "[stable-site](../b/index.md)" in rewritten
    assert "[site-root](https://microsoft.github.io/autogen/dev/user-guide/a/missing.html)" in rewritten
    assert "[archive](https://microsoft.github.io/autogen/0.2/docs/topics/x.html)" in rewritten
    assert "[anchor](#local)" in rewritten
    assert "[missing](does-not-exist.md)" in rewritten
    assert "![image](pic.png)" in rewritten
    # Links inside fenced code blocks must not be rewritten.
    assert "```python\n[inside](nb.ipynb)\n```" in rewritten


def test_strip_markdown_noise() -> None:
    text = "---\nmyst:\n  html_meta:\n    x: y\n---\n\n# Title\n\n```{toctree}\n:hidden:\n\nchild\n```\n\nBody\n"
    cleaned = llms_txt.strip_markdown_noise(text)
    assert "myst:" not in cleaned
    assert "toctree" not in cleaned
    assert "# Title" in cleaned
    assert "Body" in cleaned


def test_first_heading_ignores_fenced_code() -> None:
    text = "```md\n# Not a heading\n```\n\n# Real Heading ##\n"
    assert llms_txt.first_heading(text) == "Real Heading"


def test_build_llms_index_structure() -> None:
    sections = [
        ("Core User Guide", [("Core", "user-guide/core-user-guide/index.md")]),
        ("AgentChat User Guide", [("Quick Start", "user-guide/agentchat-user-guide/quickstart.md")]),
    ]
    output = llms_txt.build_llms_index(sections, full_document=llms_txt.LLMS_FULL_FILENAME)
    lines = output.splitlines()

    assert lines[0] == "# AutoGen"
    assert lines[1] == ""
    assert lines[2].startswith("> ")
    assert "## Core User Guide" in lines
    assert "## AgentChat User Guide" in lines
    assert "## Optional" in lines
    assert "- [Core](user-guide/core-user-guide/index.md)" in lines
    assert f"- [Full documentation]({llms_txt.LLMS_FULL_FILENAME})" in lines


def test_generate_llms_files_covers_all_guide_pages(tmp_path: Path) -> None:
    pages = llms_txt.discover_user_guide_pages(SRC_DIR)
    assert pages, "expected user-guide pages in the repository"

    outdir = tmp_path / "build"
    for page in pages:
        html = outdir / (page.docname + ".html")
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text("<!doctype html>", encoding="utf-8")

    result = llms_txt.generate_llms_files(SRC_DIR, outdir)

    assert result.index_path == outdir / llms_txt.LLMS_FILENAME
    assert result.full_path == outdir / llms_txt.LLMS_FULL_FILENAME
    assert result.index_path.is_file()
    assert result.full_path.is_file()
    assert result.full_path.read_text(encoding="utf-8").startswith("# AutoGen\n")

    llms_text = result.index_path.read_text(encoding="utf-8")
    targets = set()
    sections = 0
    for line in llms_text.splitlines():
        if line.startswith("## "):
            sections += 1
            continue
        match = LINK_LINE_RE.match(line)
        if match:
            targets.add(match.group("target"))

    assert sections >= 4
    # Every link in llms.txt must resolve to a file produced by the same build.
    for target in targets:
        assert (outdir / target).is_file(), f"dangling llms.txt link: {target}"
    assert llms_txt.LLMS_FULL_FILENAME in targets

    # Every non-index user-guide page must be referenced at least once.
    non_index = [page.docname for page in pages if not page.rel.endswith("/index.md")]
    assert len(non_index) >= 70
    for docname in non_index:
        assert docname + ".md" in targets, f"page not referenced: {docname}"

    # Per-page Markdown copies exist and internal page links were rewritten.
    for page in pages:
        md_path = outdir / (page.docname + ".md")
        assert md_path.is_file(), f"missing markdown copy: {page.rel}"
        compiled = md_path.read_text(encoding="utf-8")
        for target in ANY_LINK_RE.findall(compiled):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", target) or target.startswith(("#", "/")):
                continue
            assert not target.split("#")[0].endswith(".ipynb"), f"unrewritten notebook link in {page.rel}: {target}"


def test_generate_llms_files_skips_pages_the_build_did_not_produce(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    outdir = tmp_path / "build"
    # Only ``user-guide/a/index`` gets an HTML page in this build.
    index_html = outdir / "user-guide/a/index.html"
    index_html.parent.mkdir(parents=True, exist_ok=True)
    index_html.write_text("<!doctype html>", encoding="utf-8")

    result = llms_txt.generate_llms_files(src, outdir)

    llms_text = result.index_path.read_text(encoding="utf-8")
    assert "(user-guide/a/index.md)" in llms_text
    assert "(user-guide/a/nb.md)" not in llms_text
    assert "(user-guide/b/index.md)" not in llms_text
    assert not (outdir / "user-guide/a/nb.md").exists()


def test_group_pages_puts_section_index_first() -> None:
    pages = [
        llms_txt.GuidePage(
            rel="user-guide/core-user-guide/components/tools.md",
            docname="user-guide/core-user-guide/components/tools",
            path=Path("tools.md"),
            section="core-user-guide",
        ),
        llms_txt.GuidePage(
            rel="user-guide/core-user-guide/cookbook/index.md",
            docname="user-guide/core-user-guide/cookbook/index",
            path=Path("cookbook.md"),
            section="core-user-guide",
        ),
        llms_txt.GuidePage(
            rel="user-guide/core-user-guide/index.md",
            docname="user-guide/core-user-guide/index",
            path=Path("index.md"),
            section="core-user-guide",
        ),
    ]

    grouped = llms_txt.group_pages_by_section(pages)

    assert len(grouped) == 1
    title, section_pages = grouped[0]
    assert title == "Core User Guide"
    assert [page.docname for page in section_pages] == [
        "user-guide/core-user-guide/index",
        "user-guide/core-user-guide/cookbook/index",
        "user-guide/core-user-guide/components/tools",
    ]


@pytest.mark.parametrize("docname", ["user-guide/core-user-guide/index", "user-guide/agentchat-user-guide/quickstart"])
def test_discover_includes_docnames(docname: str) -> None:
    docnames = {page.docname for page in llms_txt.discover_user_guide_pages(SRC_DIR)}
    assert docname in docnames
