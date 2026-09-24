from datetime import date

import pytest

from mail2disk.imap_source import (
    Folder,
    MailError,
    decode_mutf7,
    imap_date,
    parse_fetch_response,
    parse_list_line,
    parse_uid_list,
    quote_mailbox,
    should_scan,
)


def test_decode_mutf7_russian():
    assert decode_mutf7("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-") == "Отправленные"
    assert decode_mutf7("Work &- Life") == "Work & Life"
    assert decode_mutf7("INBOX") == "INBOX"


@pytest.mark.parametrize(
    "line, name, display, flags",
    [
        (b'(\\HasNoChildren) "|" INBOX', "INBOX", "INBOX", {"\\hasnochildren"}),
        (
            b'(\\HasNoChildren \\Sent) "|" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"',
            "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-",
            "Отправленные",
            {"\\hasnochildren", "\\sent"},
        ),
        (b'(\\HasNoChildren) "|" "INBOX|Work Stuff"', "INBOX|Work Stuff", "INBOX/Work Stuff", {"\\hasnochildren"}),
        (b'(\\Noselect) NIL "Root"', "Root", "Root", {"\\noselect"}),
    ],
)
def test_parse_list_line(line, name, display, flags):
    folder = parse_list_line(line)
    assert folder == Folder(name=name, display_name=display, flags=frozenset(flags))


def test_parse_list_line_garbage():
    assert parse_list_line(b"garbage") is None


def folder(display, flags=()):
    return Folder(name=display, display_name=display, flags=frozenset(flags))


@pytest.mark.parametrize(
    "item, expected",
    [
        (folder("INBOX"), True),
        (folder("Работа"), True),
        (folder("Spam", ["\\junk"]), False),
        (folder("Trash", ["\\trash"]), False),
        (folder("Sent", ["\\sent"]), False),
        (folder("Drafts", ["\\drafts"]), False),
        (folder("Спам"), False),
        (folder("Исходящие"), False),
        (folder("Outbox"), False),
        (folder("Root", ["\\noselect"]), False),
    ],
)
def test_should_scan_defaults(item, expected):
    assert should_scan(item, []) is expected


def test_should_scan_with_explicit_list():
    assert should_scan(folder("INBOX"), ["inbox"]) is True
    assert should_scan(folder("Работа"), ["INBOX"]) is False
    assert should_scan(folder("Spam", ["\\junk"]), ["Spam"]) is True


def test_imap_date_does_not_depend_on_locale():
    assert imap_date(date(2026, 9, 4)) == "04-Sep-2026"


def test_parse_uid_list():
    assert parse_uid_list([b"3 1 2"]) == [1, 2, 3]
    assert parse_uid_list([b""]) == []
    assert parse_uid_list([None]) == []


def test_quote_mailbox():
    assert quote_mailbox('a "b" \\c') == '"a \\"b\\" \\\\c"'


def test_parse_fetch_response_internaldate_before_body():
    data = [(b'1 (UID 7 INTERNALDATE "24-Sep-2026 10:15:00 +0300" BODY[] {5}', b"hello"), b")"]
    raw, received = parse_fetch_response(data)
    assert raw == b"hello"
    assert received is not None and received.year == 2026 and received.month == 9


def test_parse_fetch_response_internaldate_after_body():
    data = [(b"1 (UID 7 BODY[] {5}", b"hello"), b' INTERNALDATE "01-Jan-2026 00:00:00 +0000")']
    raw, received = parse_fetch_response(data)
    assert raw == b"hello"
    assert received is not None


def test_parse_fetch_response_without_body():
    with pytest.raises(MailError):
        parse_fetch_response([b"1 (UID 7)"])
