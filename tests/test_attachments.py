import email
from email.message import EmailMessage

from mail2disk.attachments import extract_attachments

from .fakes import make_message


def parse(raw):
    return email.message_from_bytes(raw)


def test_regular_attachments_with_cyrillic_names():
    raw = make_message(attachments=[("Отчёт за сентябрь.pdf", b"PDF"), ("data.csv", b"a,b")])
    result = extract_attachments(parse(raw))
    assert [(a.filename, a.data) for a in result] == [("Отчёт за сентябрь.pdf", b"PDF"), ("data.csv", b"a,b")]


def test_message_without_attachments():
    assert extract_attachments(parse(make_message())) == []


def test_small_signature_logo_is_skipped_but_big_inline_photo_kept():
    raw = make_message(
        attachments=[("real.pdf", b"PDF")],
        inline_images=[("<logo@x>", b"x" * 500), ("<photo@x>", b"x" * 50_000)],
    )
    names = [a.filename for a in extract_attachments(parse(raw))]
    assert names[0] == "real.pdf"
    assert len(names) == 2
    assert names[1].startswith("attachment-2")


def test_inline_photo_with_filename_from_phone_is_kept():
    message = EmailMessage()
    message.set_content("фото")
    message.add_attachment(b"J" * 100, maintype="image", subtype="jpeg", disposition="inline", filename="IMG_0001.JPG")
    result = extract_attachments(parse(message.as_bytes()))
    assert [a.filename for a in result] == ["IMG_0001.JPG"]


def test_dangerous_filename_is_sanitized():
    raw = make_message(attachments=[("../../etc/passwd", b"x")])
    assert extract_attachments(parse(raw))[0].filename == "_.._etc_passwd"


def test_attachment_without_name_gets_fallback():
    raw = (
        b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=X\r\n\r\n"
        b"--X\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
        b"--X\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nUERG\r\n--X--\r\n"
    )
    result = extract_attachments(parse(raw))
    assert [(a.filename, a.data) for a in result] == [("attachment-1.pdf", b"PDF")]


def test_rfc2231_long_cyrillic_name():
    raw = (
        b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=X\r\n\r\n"
        b"--X\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
        b"--X\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment;\r\n filename*0*=utf-8''%D0%94%D0%BE%D0%B3%D0%BE;\r\n"
        b" filename*1*=%D0%B2%D0%BE%D1%80.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nUERG\r\n--X--\r\n"
    )
    assert extract_attachments(parse(raw))[0].filename == "Договор.pdf"


def test_attachments_of_forwarded_message_are_extracted():
    inner = parse(make_message(attachments=[("inner.pdf", b"IN")]))
    outer = EmailMessage()
    outer.set_content("see attached")
    outer.add_attachment(inner)
    names = [a.filename for a in extract_attachments(parse(outer.as_bytes()))]
    assert "inner.pdf" in names
