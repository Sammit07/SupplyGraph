"""Passage extraction, chunking and mention linking (no database, no network)."""

from __future__ import annotations

from conftest import SAMPLE_PDF

from supplygraph.passages import Passage, chunk_pages, extract_pages, link_mentions


def test_extract_pages_from_sample_pdf():
    pages = extract_pages((SAMPLE_PDF / "busan-port-labour-advisory.pdf").read_bytes())
    assert pages and pages[0][0] == 1
    assert "Port of Busan" in pages[0][1]


def test_extract_pages_tolerates_garbage():
    assert extract_pages(b"%PDF-1.4 not really a pdf") == []


def test_chunks_respect_size_overlap_and_pages():
    sentence = "The terminal is closed to vessels until further notice. "
    pages = [(1, sentence * 30), (2, "Short second page.")]
    chunks = chunk_pages(pages, size=300, overlap=80, limit=100)
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[-1].page == 2 and chunks[-1].text == "Short second page."
    page1 = [c for c in chunks if c.page == 1]
    # consecutive passages share trailing context
    assert page1[1].text.startswith(page1[0].text.split(". ")[-1][:20])
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_chunk_limit_and_hard_wrap():
    pages = [(1, "x" * 5000)]
    chunks = chunk_pages(pages, size=1000, overlap=0, limit=3)
    assert len(chunks) == 3 and all(len(c.text) <= 1000 for c in chunks)


def test_link_mentions_whole_word_case_insensitive():
    passages = [
        Passage(0, 1, "No loading at the PORT OF BUSAN container terminals."),
        Passage(1, 1, "Busanport is a different word."),
        Passage(2, 1, "Nakdong Display Co., Ltd. declared force majeure."),
    ]
    link_mentions(
        passages,
        {
            "PORT-BUSAN": {"Port of Busan"},
            "SUP-033": {"Nakdong Display", "Nakdong Display Co., Ltd."},
        },
    )
    assert passages[0].mentions == {"PORT-BUSAN"}
    assert passages[1].mentions == set()
    assert passages[2].mentions == {"SUP-033"}


def test_page_footers_are_dropped():
    from supplygraph.passages import _PAGE_FOOTER

    assert _PAGE_FOOTER.match("Page 1") and _PAGE_FOOTER.match("  page 3 of 10 ")
    assert not _PAGE_FOOTER.match("See page 3 for details")
    pages = extract_pages((SAMPLE_PDF / "penang-microcontrollers-cyber-incident.pdf").read_bytes())
    assert not pages[0][1].rstrip().endswith("Page 1")


def test_keyword_overlap_ranks_the_passage_that_says_it():
    from supplygraph.tools import keyword_overlap, query_terms

    terms = query_terms("when will shipments resume")
    assert terms == ["shipment", "resume"]
    answer = "We expect to restore order processing and resume shipments on 10 November 2026."
    other = "We will issue updates every 48 hours. Contact your account manager."
    assert keyword_overlap(terms, answer) == 1.0
    assert keyword_overlap(terms, other) == 0.0
