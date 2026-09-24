import pytest

from mail2disk.names import decode_filename, numbered_filename, safe_filename


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("=?utf-8?b?0J7RgtGH0ZHRgi5wZGY=?=", "Отчёт.pdf"),
        ("=?koi8-r?b?68/O1NLBy9QuZG9jeA==?=", "Контракт.docx"),
        ("=?windows-1251?q?=D1=F7=E5=F2.xlsx?=", "Счет.xlsx"),
        ("plain.txt", "plain.txt"),
        (None, ""),
        ("", ""),
    ],
)
def test_decode_filename(raw, expected):
    assert decode_filename(raw) == expected


def test_decode_unknown_charset_does_not_crash():
    assert decode_filename("=?unknown-8bit?q?abc=2Epdf?=") == "abc.pdf"


def test_decode_raw_utf8_bytes_in_header():
    raw = "Отчёт.pdf".encode("utf-8").decode("ascii", errors="surrogateescape")
    assert decode_filename(raw) == "Отчёт.pdf"


def test_decode_raw_cp1251_bytes_in_header():
    raw = "Отчёт.pdf".encode("cp1251").decode("ascii", errors="surrogateescape")
    assert decode_filename(raw) == "Отчёт.pdf"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("../../evil.sh", "_.._evil.sh"),
        ("a/b\\c:d*e?f\"g<h>i|j.pdf", "a_b_c_d_e_f_g_h_i_j.pdf"),
        ("  отчёт .pdf ", "отчёт .pdf"),
        ("...", "attachment"),
        ("", "attachment"),
        ("CON.txt", "_CON.txt"),
        ("line\nbreak.txt", "line_break.txt"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_truncates_but_keeps_extension():
    result = safe_filename("а" * 400 + ".pdf")
    assert len(result) == 150
    assert result.endswith(".pdf")


@pytest.mark.parametrize(
    "name, number, expected",
    [
        ("invoice.pdf", 1, "invoice.pdf"),
        ("invoice.pdf", 2, "invoice (2).pdf"),
        ("archive.tar.gz", 3, "archive.tar (3).gz"),
        ("README", 2, "README (2)"),
    ],
)
def test_numbered_filename(name, number, expected):
    assert numbered_filename(name, number) == expected
