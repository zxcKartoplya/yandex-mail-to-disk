import mimetypes
from dataclasses import dataclass
from email.message import Message

from .names import decode_filename, safe_filename


@dataclass
class Attachment:
    filename: str
    data: bytes
    content_type: str


def _is_decorative_image(part: Message, size: int, min_inline_image_bytes: int) -> bool:
    return (
        part.get_content_disposition() != "attachment"
        and part.get_content_maintype() == "image"
        and bool(part.get("Content-ID"))
        and size < min_inline_image_bytes
    )


def _fallback_name(part: Message, index: int) -> str:
    extension = mimetypes.guess_extension(part.get_content_type()) or ".bin"
    return f"attachment-{index}{extension}"


def extract_attachments(message: Message, min_inline_image_bytes: int = 20 * 1024) -> list[Attachment]:
    result = []
    for part in message.walk():
        if part.is_multipart():
            continue
        raw_name = part.get_filename()
        is_body_part = (
            not raw_name
            and part.get_content_disposition() != "attachment"
            and part.get_content_maintype() != "image"
        )
        if is_body_part:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        if _is_decorative_image(part, len(payload), min_inline_image_bytes):
            continue
        fallback = _fallback_name(part, len(result) + 1)
        name = safe_filename(decode_filename(raw_name), fallback=fallback) if raw_name else fallback
        result.append(Attachment(filename=name, data=payload, content_type=part.get_content_type()))
    return result
