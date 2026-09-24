import base64
import imaplib
import re
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import date, datetime

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$', re.IGNORECASE)
STATUS_RE = re.compile(r"(UIDNEXT|UIDVALIDITY)\s+(\d+)", re.IGNORECASE)
SKIP_FLAGS = {"\\noselect", "\\nonexistent", "\\junk", "\\spam", "\\trash", "\\sent", "\\drafts", "\\templates", "\\all"}
SKIP_NAMES = {
    "spam", "junk", "trash", "sent", "drafts", "outbox",
    "спам", "удаленные", "удалённые", "отправленные", "черновики", "исходящие", "корзина",
}


class MailError(Exception):
    pass


class MailAuthError(MailError):
    pass


class MailConnectionError(MailError):
    pass


@dataclass(frozen=True)
class Folder:
    name: str
    display_name: str
    flags: frozenset[str]


def decode_mutf7(value: str) -> str:
    def replace(match: re.Match) -> str:
        chunk = match.group(1)
        if not chunk:
            return "&"
        padded = chunk.replace(",", "/") + "=" * (-len(chunk) % 4)
        try:
            return base64.b64decode(padded).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", replace, value)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def quote_mailbox(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_list_line(line: bytes | str) -> Folder | None:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
    match = LIST_RE.match(text.strip())
    if not match:
        return None
    flags = frozenset(flag.lower() for flag in match.group("flags").split())
    name = _unquote(match.group("name"))
    delimiter = _unquote(match.group("delimiter")) if match.group("delimiter").upper() != "NIL" else ""
    display = decode_mutf7(name)
    if delimiter:
        display = display.replace(delimiter, "/")
    return Folder(name=name, display_name=display, flags=flags)


def should_scan(folder: Folder, include: list[str]) -> bool:
    if include:
        wanted = {item.casefold() for item in include}
        return folder.name.casefold() in wanted or folder.display_name.casefold() in wanted
    if folder.flags & SKIP_FLAGS:
        return False
    return folder.display_name.casefold() not in SKIP_NAMES


def imap_date(day: date) -> str:
    return f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"


def parse_uid_list(data: list) -> list[int]:
    uids = set()
    for chunk in data:
        if not chunk:
            continue
        text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        uids.update(int(item) for item in text.split() if item.isdigit())
    return sorted(uids)


def parse_fetch_response(data: list) -> tuple[bytes, datetime | None]:
    raw = None
    meta = b""
    for item in data:
        if isinstance(item, tuple):
            meta += item[0] + b" "
            if raw is None and len(item) > 1 and isinstance(item[1], bytes):
                raw = item[1]
        elif isinstance(item, bytes):
            meta += item + b" "
    if raw is None:
        raise MailError("Сервер не вернул содержимое письма.")
    parsed = imaplib.Internaldate2tuple(meta)
    received = datetime.fromtimestamp(time.mktime(parsed)) if parsed else None
    return raw, received


def _friendly_login_error(error: Exception) -> str:
    text = str(error)
    if "AUTHENTICATIONFAILED" in text.upper() or "INVALID" in text.upper():
        return (
            "Яндекс Почта не пустила: неверный адрес или пароль приложения, либо в настройках Почты "
            "не включён доступ по IMAP (Все настройки → Почтовые программы)."
        )
    return f"Не удалось войти в почту: {text}"


class MailSource:
    def __init__(self, server: str, email: str, password: str, timeout: int = 60):
        self.server = server
        self.email = email
        self.password = password
        self.timeout = timeout
        self.conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "MailSource":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self.conn = imaplib.IMAP4_SSL(self.server, timeout=self.timeout)
        except (OSError, ssl.SSLError, socket.timeout) as error:
            raise MailConnectionError(f"Нет связи с почтовым сервером {self.server}: {error}") from error
        try:
            self.conn.login(self.email, self.password)
        except imaplib.IMAP4.error as error:
            self.close()
            raise MailAuthError(_friendly_login_error(error)) from error

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        self.conn = None

    def _call(self, command: str, *args) -> list:
        if self.conn is None:
            raise MailError("Нет подключения к почте.")
        try:
            status, data = getattr(self.conn, command)(*args)
        except (imaplib.IMAP4.abort, OSError) as error:
            raise MailConnectionError(f"Связь с почтовым сервером прервалась: {error}") from error
        except imaplib.IMAP4.error as error:
            raise MailError(f"Ошибка почтового сервера ({command}): {error}") from error
        if status != "OK":
            raise MailError(f"Почтовый сервер ответил {status} на {command}: {data}")
        return data

    def list_folders(self) -> list[Folder]:
        folders = []
        for line in self._call("list"):
            if isinstance(line, tuple):
                name = line[1].replace(b"\\", b"\\\\").replace(b'"', b'\\"')
                line = re.sub(rb"\{\d+\}\s*$", b"", line[0]) + b'"' + name + b'"'
            folder = parse_list_line(line) if line else None
            if folder is not None:
                folders.append(folder)
        return folders

    def folder_status(self, name: str) -> tuple[int, int]:
        data = self._call("status", quote_mailbox(name), "(UIDNEXT UIDVALIDITY)")
        text = b" ".join(item for item in data if isinstance(item, bytes)).decode(errors="replace")
        values = {key.upper(): int(value) for key, value in STATUS_RE.findall(text)}
        if "UIDVALIDITY" not in values or "UIDNEXT" not in values:
            raise MailError(f"Не удалось получить состояние папки {name}: {text}")
        return values["UIDVALIDITY"], values["UIDNEXT"]

    def open_folder(self, name: str) -> None:
        self._call("select", quote_mailbox(name), True)

    def search_since(self, day: date) -> list[int]:
        return parse_uid_list(self._call("uid", "SEARCH", None, "SINCE", imap_date(day)))

    def search_after(self, last_uid: int) -> list[int]:
        found = parse_uid_list(self._call("uid", "SEARCH", None, "UID", f"{last_uid + 1}:*"))
        return [uid for uid in found if uid > last_uid]

    def fetch(self, uid: int) -> tuple[bytes, datetime | None]:
        return parse_fetch_response(self._call("uid", "FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])"))
