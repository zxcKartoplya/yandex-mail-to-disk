import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage

from mail2disk.disk import DiskAuthError, DiskConnectionError, DiskError, RemoteFile
from mail2disk.imap_source import Folder, MailError
from mail2disk.state import State


def make_message(subject="Тема", attachments=(), inline_images=(), body="Привет"):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "sender@example.com"
    message["To"] = "me@yandex.ru"
    message.set_content(body)
    for name, data in attachments:
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    for cid, data in inline_images:
        message.add_attachment(data, maintype="image", subtype="png", disposition="inline", cid=cid)
    return message.as_bytes()


@dataclass
class FakeFolder:
    uidvalidity: int = 1
    messages: dict[int, tuple[bytes, datetime]] = field(default_factory=dict)
    flags: frozenset = frozenset()

    @property
    def uidnext(self) -> int:
        return max(self.messages, default=0) + 1


class FakeMail:
    def __init__(self, folders: dict[str, FakeFolder]):
        self.folders = folders
        self.fetch_errors: dict[int, int] = {}
        self.fetched: list[tuple[str, int]] = []
        self.current = ""
        self.connected = False
        self.since_calls: list[date] = []

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def list_folders(self):
        return [Folder(name=name, display_name=name, flags=f.flags) for name, f in self.folders.items()]

    def folder_status(self, name):
        folder = self.folders[name]
        return folder.uidvalidity, folder.uidnext

    def open_folder(self, name):
        self.current = name

    def search_since(self, day):
        self.since_calls.append(day)
        return sorted(uid for uid, (_, received) in self.folders[self.current].messages.items() if received.date() >= day)

    def search_after(self, last_uid):
        return sorted(uid for uid in self.folders[self.current].messages if uid > last_uid)

    def fetch(self, uid):
        if self.fetch_errors.get(uid, 0) > 0:
            self.fetch_errors[uid] -= 1
            raise MailError(f"сбой при чтении {uid}")
        self.fetched.append((self.current, uid))
        return self.folders[self.current].messages[uid]


class FakeDisk:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.folders: set[str] = set()
        self.fail_uploads = 0
        self.network_down = 0
        self.auth_broken = False

    def check(self):
        if self.auth_broken:
            raise DiskAuthError("нет доступа")

    def ensure_folder(self, path):
        if self.auth_broken:
            raise DiskAuthError("нет доступа")
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            self.folders.add("/".join(parts[:i]))

    def info(self, path):
        data = self.files.get(path)
        return None if data is None else RemoteFile(size=len(data), md5=hashlib.md5(data).hexdigest())

    def upload(self, path, data, overwrite=False):
        if self.auth_broken:
            raise DiskAuthError("нет доступа")
        if self.network_down > 0:
            self.network_down -= 1
            raise DiskConnectionError("нет связи с Яндекс Диском")
        if self.fail_uploads > 0:
            self.fail_uploads -= 1
            raise DiskError("Диск временно недоступен")
        if path in self.files and not overwrite:
            raise DiskError("файл уже существует")
        self.files[path] = data

    def download(self, path):
        return self.files.get(path)


class MemoryStore:
    def __init__(self):
        self.state = State()
        self.saves = 0

    def load(self):
        return State.from_json(self.state.to_json())

    def save(self, state):
        self.saves += 1
        self.state = State.from_json(state.to_json())
