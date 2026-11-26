"""Generate ``llms.txt`` (and companion Markdown) for the AutoGen user guide.

The generator runs from Sphinx's ``build-finished`` event, so the artifacts are
produced automatically by the regular ``poe docs-build`` / ``poe docs-check``
flow and land next to the HTML pages in the build output directory (the GitHub
Pages site root).

For every user-guide page a Markdown copy is written to the build directory at
the same relative path as the HTML page.  ``llms.txt`` links to those Markdown
files, and ``llms-full.txt`` concatenates all of them.  Internal links inside
the Markdown copies are rewritten so that links to user-guide pages point at the
same Markdown targets referenced by ``llms.txt`` instead of the GitHub sources.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - sphinx is only available during a docs build
    from sphinx.application import Sphinx

LLMS_FILENAME = "llms.txt"
LLMS_FULL_FILENAME = "llms-full.txt"

_SOURCE_SUFFIXES = (".md", ".ipynb")
# Suffixes that can be resolved from a link target (``.rst`` pages are generated
# during the build, e.g. the API reference).
_RESOLVABLE_SUFFIXES = (".md", ".ipynb", ".rst")

INTRO = (
    "AutoGen is a framework for building AI agents and applications. "
    "This file lists the AutoGen user guide documentation pages, which are available "
    "as Markdown for large language models and AI agents."
)

# Order and display names of the user-guide sections (sub-directories).
GUIDE_TITLES: Dict[str, str] = {
    "core-user-guide": "Core User Guide",
    "agentchat-user-guide": "AgentChat User Guide",
    "extensions-user-guide": "Extensions User Guide",
    "autogenstudio-user-guide": "AutoGen Studio User Guide",
}
GUIDE_ORDER: Tuple[str, ...] = (
    "core-user-guide",
    "agentchat-user-guide",
    "extensions-user-guide",
    "autogenstudio-user-guide",
)
_DEFAULT_SECTION_TITLE = "User Guide"


class GuidePage(NamedTuple):
    """A user-guide source page discovered on disk."""

    rel: str  # POSIX path relative to the Sphinx source directory
    docname: str  # relative path without the source suffix
    path: Path  # absolute path to the source file
    section: str  # immediate sub-directory under ``user-guide``


# ---------------------------------------------------------------------------
# Source discovery and rendering
# ---------------------------------------------------------------------------


def discover_user_guide_pages(srcdir: Path) -> List[GuidePage]:
    """Return every ``.md``/``.ipynb`` page under ``<srcdir>/user-guide``."""
    root = Path(srcdir) / "user-guide"
    pages: List[GuidePage] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        rel = path.relative_to(Path(srcdir)).as_posix()
        docname = rel[: -len(path.suffix)]
        parts = rel.split("/")
        section = parts[1] if len(parts) > 2 else ""
        pages.append(GuidePage(rel=rel, docname=docname, path=path, section=section))
    return pages


def render_notebook(nb: Dict) -> str:
    """Render a parsed notebook as Markdown.

    Markdown cells are emitted verbatim and code cells are emitted as fenced
    ``python`` blocks.  Cell outputs are intentionally dropped.
    """
    parts: List[str] = []
    for cell in nb.get("cells", []):
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        if not isinstance(source, str) or not source.strip():
            continue
        cell_type = cell.get("cell_type")
        if cell_type == "markdown":
            parts.append(source.strip("\n"))
        elif cell_type == "code":
            parts.append("```python\n" + source.strip("\n") + "\n```")
    return "\n\n".join(parts)


def render_source(path: Path) -> str:
    """Return the source page as Markdown (notebooks are rendered)."""
    if path.suffix == ".ipynb":
        return render_notebook(json.loads(path.read_text(encoding="utf-8")))
    return path.read_text(encoding="utf-8")


_FRONT_MATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
# ``{toctree}`` directive blocks (MyST fenced and colon-fenced variants).
_TOCTREE_RE = re.compile(r"(?m)^(?P<fence>`{3,}|:{3,})\{toctree\}.*?^(?P=fence)\s*$", re.DOTALL)


def strip_markdown_noise(text: str) -> str:
    """Drop YAML front matter and ``toctree`` directives from a Markdown page."""
    text = _FRONT_MATTER_RE.sub("", text)
    return _TOCTREE_RE.sub("", text)


def _iter_code_flags(lines: Sequence[str]) -> "List[Tuple[str, bool]]":
    """Pair each line with whether it belongs to a fenced code block.

    MyST directive fences (````` ```{note} `````, ``:::{note}``) are not code, so
    links inside them are still processed.  ``:::` fences are not recognised by
    the regex at all, while ```` ```{...} ```` fences are tracked separately.
    """
    result: List[Tuple[str, bool]] = []
    in_code = False
    code_char = ""
    in_directive = False
    for line in lines:
        marker = _FENCE_START_RE.match(line)
        if marker:
            fence = marker.group(1)
            info = line[marker.end() :].strip()
            if in_directive:
                in_directive = False
            elif in_code:
                if fence[0] == code_char and not info:
                    in_code = False
            elif info.startswith("{"):
                in_directive = True
            else:
                in_code, code_char = True, fence[0]
            result.append((line, True))
        else:
            result.append((line, in_code))
    return result


def first_heading(text: str) -> Optional[str]:
    """Return the text of the first level-one heading, ignoring fenced code."""
    for line, is_code in _iter_code_flags(text.splitlines()):
        if is_code:
            continue
        match = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if match:
            return match.group(1).strip()
    return None


def page_title(page: GuidePage) -> str:
    """Best-effort page title from the source, falling back to the file name."""
    try:
        heading = first_heading(render_source(page.path))
    except (OSError, ValueError, json.JSONDecodeError):
        heading = None
    if heading:
        return heading
    stem = posixpath.splitext(posixpath.basename(page.rel))[0]
    if stem == "index":
        return GUIDE_TITLES.get(page.section, _DEFAULT_SECTION_TITLE)
    return stem.replace("-", " ").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+(?:\"[^\"]*\"|'[^']*'))?\)")
_FENCE_START_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def docname_for_target(target_path: str, page_rel: str, srcdir: Path) -> Optional[str]:
    """Resolve a Markdown link target to the source docname it references.

    ``target_path`` is the path part of a relative link (without any anchor)
    found in the page at ``page_rel``.  Returns the referenced docname when the
    target resolves to a ``.md``/``.ipynb`` file (or a directory ``index``),
    otherwise ``None``.
    """
    if not target_path:
        return None
    base = posixpath.dirname(page_rel)
    candidate = posixpath.normpath(posixpath.join(base, target_path))
    if candidate in ("", ".", "..") or candidate.startswith("../"):
        return None

    root = Path(srcdir)
    path = root / candidate
    stem = candidate[: -len(path.suffix)] if path.suffix in _RESOLVABLE_SUFFIXES else candidate
    for suffix in _RESOLVABLE_SUFFIXES:
        if (root / (stem + suffix)).is_file():
            return stem
    for suffix in _RESOLVABLE_SUFFIXES:
        index_name = posixpath.join(stem, "index" + suffix)
        if (root / index_name).is_file():
            return posixpath.join(stem, "index")
    return None


def _relative_link(dest: str, page_rel: str) -> str:
    page_dir = posixpath.dirname(page_rel)
    if not page_dir:
        return posixpath.normpath(dest)
    return posixpath.relpath(dest, page_dir)


def _rewrite_link(
    match: "re.Match[str]",
    page_rel: str,
    srcdir: Path,
    target_for_doc: Callable[[str], Optional[str]],
) -> str:
    is_image, label, target, title = match.group(1), match.group(2), match.group(3), match.group(4) or ""
    if is_image:
        return match.group(0)
    if _HAS_SCHEME_RE.match(target) or target.startswith(("#", "/", "<", "{")):
        return match.group(0)
    path, sep, anchor = target.partition("#")
    docname = docname_for_target(path, page_rel, srcdir)
    if docname is None:
        return match.group(0)
    dest = target_for_doc(docname)
    if dest is None:
        return match.group(0)
    link = _relative_link(dest, page_rel)
    return f"[{label}]({link}{sep}{anchor}{title})"


def rewrite_markdown_links(
    text: str,
    page_rel: str,
    srcdir: Path,
    target_for_doc: Callable[[str], Optional[str]],
) -> str:
    """Rewrite relative page links so they point at the generated Markdown pages.

    Links inside fenced code blocks, image links and external/absolute links are
    left untouched.
    """
    lines: List[str] = []
    for line, is_code in _iter_code_flags(text.splitlines()):
        if is_code:
            lines.append(line)
            continue
        lines.append(_LINK_RE.sub(lambda m: _rewrite_link(m, page_rel, srcdir, target_for_doc), line))
    result = "\n".join(lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def compile_page_markdown(
    page: GuidePage,
    srcdir: Path,
    target_for_doc: Callable[[str], Optional[str]],
) -> str:
    """Compile one source page into the Markdown copy written to the build."""
    content = render_source(page.path)
    if page.path.suffix == ".md":
        content = strip_markdown_noise(content)
    content = rewrite_markdown_links(content, page.rel, srcdir, target_for_doc)
    return content.strip("\n") + "\n"


# ---------------------------------------------------------------------------
# llms.txt / llms-full.txt rendering
# ---------------------------------------------------------------------------

SectionEntries = Sequence[Tuple[str, str]]


def build_llms_index(
    sections: Sequence[Tuple[str, SectionEntries]],
    *,
    project: str = "AutoGen",
    intro: str = INTRO,
    full_document: Optional[str] = None,
) -> str:
    """Render the ``llms.txt`` index following the llmstxt.org structure."""
    lines: List[str] = [f"# {project}", "", f"> {intro}", ""]
    for section_title, entries in sections:
        lines.extend([f"## {section_title}", ""])
        for entry_title, target in entries:
            lines.append(f"- [{entry_title}]({target})")
        lines.append("")
    if full_document:
        lines.extend(["## Optional", "", f"- [Full documentation]({full_document})", ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def build_llms_full(
    sections: Sequence[Tuple[str, Sequence[Tuple[str, str, str]]]],
    *,
    project: str = "AutoGen",
    intro: str = INTRO,
) -> str:
    """Render ``llms-full.txt``: every page's Markdown concatenated."""
    lines: List[str] = [f"# {project}", "", f"> {intro}", ""]
    for _section_title, pages in sections:
        for title, target, content in pages:
            lines.extend([f"# {title}", "", f"Source: [{target}]({target})", "", content.strip("\n"), ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def group_pages_by_section(pages: Sequence[GuidePage]) -> List[Tuple[str, List[GuidePage]]]:
    """Group pages into ordered ``(section title, pages)`` pairs.

    Pages are ordered with ``index`` pages first, then by path.
    """
    grouped: Dict[str, List[GuidePage]] = {}
    for page in pages:
        grouped.setdefault(page.section, []).append(page)

    def order(section: str) -> Tuple[int, str]:
        if section in GUIDE_ORDER:
            return (GUIDE_ORDER.index(section), "")
        return (len(GUIDE_ORDER), section)

    result: List[Tuple[str, List[GuidePage]]] = []
    for section in sorted(grouped, key=order):
        section_pages = sorted(
            grouped[section],
            key=lambda p: (0 if posixpath.basename(p.rel).startswith("index.") else 1, p.rel),
        )
        result.append((GUIDE_TITLES.get(section, _DEFAULT_SECTION_TITLE), section_pages))
    return result


# ---------------------------------------------------------------------------
# Sphinx integration
# ---------------------------------------------------------------------------


class GeneratedFiles(NamedTuple):
    """Paths written by :func:`generate_llms_files`."""

    pages: List[Path]
    index: Path
    full: Path


def generate_llms_files(srcdir: Path, outdir: Path, *, project: str = "AutoGen") -> GeneratedFiles:
    """Write the Markdown pages, ``llms.txt`` and ``llms-full.txt``.

    Only pages whose HTML output exists in ``outdir`` are included, guaranteeing
    that ``llms.txt`` never links to a page that the build did not produce.
    """
    srcdir = Path(srcdir)
    outdir = Path(outdir)

    pages = [page for page in discover_user_guide_pages(srcdir) if (outdir / (page.docname + ".html")).is_file()]

    user_guide_docnames = {page.docname for page in pages}

    def target_for_doc(docname: str) -> Optional[str]:
        if docname in user_guide_docnames:
            return docname + ".md"
        # Any other resolvable doc (e.g. the generated API reference) is only
        # available as HTML; point there so the compiled Markdown has no
        # dangling internal links.
        return docname + ".html"

    md_paths: List[Path] = []
    index_sections: List[Tuple[str, List[Tuple[str, str]]]] = []
    full_sections: List[Tuple[str, List[Tuple[str, str, str]]]] = []

    for section_title, section_pages in group_pages_by_section(pages):
        index_entries: List[Tuple[str, str]] = []
        full_entries: List[Tuple[str, str, str]] = []
        for page in section_pages:
            content = compile_page_markdown(page, srcdir, target_for_doc)
            md_target = page.docname + ".md"
            md_path = outdir / md_target
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content, encoding="utf-8")
            md_paths.append(md_path)
            title = page_title(page)
            index_entries.append((title, md_target))
            full_entries.append((title, md_target, content))
        index_sections.append((section_title, index_entries))
        full_sections.append((section_title, full_entries))

    llms_path = outdir / LLMS_FILENAME
    llms_path.write_text(
        build_llms_index(index_sections, project=project, full_document=LLMS_FULL_FILENAME),
        encoding="utf-8",
    )
    full_path = outdir / LLMS_FULL_FILENAME
    full_path.write_text(build_llms_full(full_sections, project=project), encoding="utf-8")
    md_paths.extend([llms_path, full_path])
    return GeneratedFiles(pages=md_paths, index=llms_path, full=full_path)


def _on_build_finished(app: "Sphinx", exception: Optional[Exception]) -> None:
    if exception is not None:
        return
    if getattr(app.builder, "format", None) != "html":
        return
    from sphinx.util import logging

    logger = logging.getLogger(__name__)
    try:
        result = generate_llms_files(Path(app.srcdir), Path(app.outdir))
        logger.info("generated %s and %s", result.index.name, result.full.name)
    except Exception as exc:  # pragma: no cover - defensive: never break the docs build
        logger.info("llms.txt generation skipped: %s", exc)


def setup(app: "Sphinx") -> Dict[str, object]:
    app.connect("build-finished", _on_build_finished)
    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
