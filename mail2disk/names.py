import re
import unicodedata
from email.errors import HeaderParseError
from email.header import decode_header, make_header

FORBIDDEN_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
MAX_NAME_LENGTH = 150
MAX_EXTENSION_LENGTH = 20
FALLBACK_CHARSETS = ("utf-8", "cp1251", "koi8-r")


def _decode_bytes(data: bytes, charset: str | None) -> str:
    candidates = ([charset] if charset else []) + list(FALLBACK_CHARSETS)
    for candidate in candidates:
        try:
            return data.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _repair_surrogates(value: str) -> str:
    if not any("\udc80" <= ch <= "\udcff" for ch in value):
        return value
    return _decode_bytes(value.encode("utf-8", errors="surrogateescape"), None)


def decode_filename(value: str | None) -> str:
    if not value:
        return ""
    value = _repair_surrogates(value)
    if "=?" not in value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError, HeaderParseError, ValueError):
        pass
    try:
        chunks = decode_header(value)
    except HeaderParseError:
        return value
    return "".join(
        _decode_bytes(chunk, charset) if isinstance(chunk, bytes) else chunk
        for chunk, charset in chunks
    )


def split_extension(name: str) -> tuple[str, str]:
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or len(extension) > MAX_EXTENSION_LENGTH or " " in extension:
        return name, ""
    return stem, "." + extension


def safe_filename(name: str, fallback: str = "attachment") -> str:
    name = unicodedata.normalize("NFC", name)
    name = FORBIDDEN_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    stem, extension = split_extension(name)
    if stem.upper() in WINDOWS_RESERVED:
        stem = "_" + stem
    if len(stem) + len(extension) > MAX_NAME_LENGTH:
        stem = stem[: MAX_NAME_LENGTH - len(extension)].rstrip(" .") or fallback
    return stem + extension


def numbered_filename(name: str, number: int) -> str:
    if number <= 1:
        return name
    stem, extension = split_extension(name)
    return f"{stem} ({number}){extension}"
