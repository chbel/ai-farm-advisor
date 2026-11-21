"""Extract citable text sections from source documents.

Each returned section is (location, text), where location is what a citation
points to: "p. 12" for PDFs, or the section heading path for JATS articles.
"""

import io
import re
import xml.etree.ElementTree as ET

from pypdf import PdfReader

_SPACES = re.compile(r"[ \t ]+")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def clean_text(text: str) -> str:
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # re-join words split across lines
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def pdf_sections(data: bytes) -> list[tuple[str, str]]:
    """One section per PDF page with meaningful text (cited as 'p. N')."""
    reader = PdfReader(io.BytesIO(data))
    sections = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = clean_text(page.extract_text() or "")
        except Exception:  # a broken page must not abort a whole document
            continue
        if len(text) >= 200:  # skip blank, title-only and image-only pages
            sections.append((f"p. {number}", text))
    return sections


# JATS (Journal Article Tag Suite) XML, as served by Europe PMC.
_SKIP_SECTIONS = re.compile(
    r"^(references?|acknowledg|funding|author contributions?|conflicts? of interest|"
    r"data availability|supplementary|abbreviations|institutional review|informed consent)",
    re.IGNORECASE,
)


# Elements whose content is not running text. Their tail (the text after them)
# still belongs to the paragraph and is kept.
_SKIP_ELEMENTS = {"xref", "table-wrap", "fig", "disp-formula", "supplementary-material"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # strip an XML namespace if present


def _raw_text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if _local(child.tag) not in _SKIP_ELEMENTS:
            parts.append(_raw_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _text_of(element: ET.Element) -> str:
    return clean_text(_raw_text(element))


def jats_sections(xml: bytes) -> list[tuple[str, str]]:
    """Abstract + body sections of a JATS article (cited by section heading)."""
    root = ET.fromstring(xml)
    sections: list[tuple[str, str]] = []

    abstract = root.find(".//article-meta/abstract")
    if abstract is not None:
        text = " ".join(_text_of(p) for p in abstract.iter("p"))
        if text:
            sections.append(("Abstract", text))

    def walk(sec: ET.Element, path: list[str]) -> None:
        title_el = sec.find("title")
        title = _text_of(title_el) if title_el is not None else ""
        if title and _SKIP_SECTIONS.match(title):
            return
        here = path + ([title] if title else [])
        paragraphs = [_text_of(p) for p in sec.findall("p")]
        text = "\n".join(p for p in paragraphs if p)
        if text:
            sections.append((" > ".join(here)[:200] or "Body", text))
        for child in sec.findall("sec"):
            walk(child, here)

    body = root.find(".//body")
    if body is not None:
        loose = "\n".join(_text_of(p) for p in body.findall("p"))
        if loose:
            sections.append(("Body", loose))
        for sec in body.findall("sec"):
            walk(sec, [])
    return sections
