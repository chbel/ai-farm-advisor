"""Manifest, parsing and chunking (no network, no database)."""

import pytest

from app.knowledge.chunking import is_prose, make_chunks
from app.knowledge.ingest import load_manifest
from app.knowledge.parsing import clean_text, jats_sections


def test_manifest_is_valid_and_licenses_are_consistent():
    specs = load_manifest()

    assert len(specs) >= 8
    assert len({s.id for s in specs}) == len(specs)
    for spec in specs:
        # Only CC BY sources may be marked usable commercially.
        if spec.commercial_use:
            assert spec.license.startswith("CC BY") and "NC" not in spec.license, spec.id
        assert spec.extra.get("license_checked"), f"{spec.id}: record how the license was verified"
        if spec.fetch == "europepmc":
            assert spec.pmcid.startswith("PMC")
    assert any(not s.commercial_use for s in specs)  # restricted sources are flagged, not hidden


def test_manifest_rejects_bad_entries(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {id: a, title: t, organization: o, year: 2020, language: en, crops: [wheat],"
        " topics: [x], license: CC BY 4.0, commercial_use: true, fetch: pdf, url: u}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="crops"):
        load_manifest(bad)


def test_clean_text_joins_hyphenated_words_and_spaces():
    assert clean_text("irri-\ngation  needs  water\n\n\n  ok ") == "irrigation needs water\nok"


JATS = b"""<article><front><article-meta>
<abstract><p>Copper fungicides are the standard control.</p></abstract>
</article-meta></front><body>
<sec><title>1. Introduction</title>
  <p>Olive leaf spot<xref ref-type="bibr">[12]</xref> is widespread in the Mediterranean.</p>
  <sec><title>1.1 Symptoms</title><p>Circular spots appear on leaves.</p>
    <fig><caption><p>Figure text to skip</p></caption></fig></sec>
</sec>
<sec><title>References</title><p>Smith J. 1999.</p></sec>
</body></article>"""


def test_jats_sections_keep_text_after_citations_and_skip_figures_and_references():
    sections = dict(jats_sections(JATS))

    assert sections["Abstract"] == "Copper fungicides are the standard control."
    assert sections["1. Introduction"] == "Olive leaf spot is widespread in the Mediterranean."
    assert sections["1. Introduction > 1.1 Symptoms"] == "Circular spots appear on leaves."
    assert not any("Figure text" in text for text in sections.values())
    assert not any("Smith" in text for text in sections.values())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The crop coefficient for olives during mid-season is 0.70 in FAO-56.", True),
        ("Le coefficient cultural de l'olivier est de 0,70.", True),
        ("الزيتون يحتاج إلى الري في الصيف", True),
        ("15\n0.15\n0.15\n1.15\n1.15\n1.05\n0.95\n0.90 0.70 0.85", False),
        ("0 12.3 10.7 9.2 8.5 46 15.2 13.9 Tmax 12.4", False),
        ("   ", False),
    ],
)
def test_is_prose_rejects_flattened_tables(text, expected):
    assert is_prose(text) is expected


def test_chunks_respect_token_budget_and_keep_locations():
    words = lambda text: text.split()  # noqa: E731 - whitespace "tokenizer" for the test
    long_text = " ".join(f"Sentence number {i} about olive irrigation." for i in range(200))
    chunks = make_chunks([("p. 3", long_text), ("p. 4", "too short")], words,
                         chunk_tokens=60, overlap_tokens=10)

    assert len(chunks) > 5
    assert all(c.location == "p. 3" for c in chunks)  # the 9-character page is dropped
    assert all(len(words(c.text)) <= 60 for c in chunks)
