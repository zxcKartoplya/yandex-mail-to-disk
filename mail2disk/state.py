import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class FolderState:
    uidvalidity: int
    last_uid: int


@dataclass
class State:
    folders: dict[str, FolderState] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    last_success: str | None = None

    def to_json(self) -> str:
        data = {
            "folders": {name: vars(fs) for name, fs in self.folders.items()},
            "failures": self.failures,
            "last_success": self.last_success,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "State":
        data = json.loads(text)
        return cls(
            folders={name: FolderState(**fs) for name, fs in data.get("folders", {}).items()},
            failures={key: int(value) for key, value in data.get("failures", {}).items()},
            last_success=data.get("last_success"),
        )


class FileStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> State:
        if not self.path.exists():
            return State()
        try:
            return State.from_json(self.path.read_text(encoding="utf-8"))
        except (ValueError, TypeError, KeyError) as error:
            broken = self.path.with_suffix(".broken.json")
            os.replace(self.path, broken)
            log.warning("Файл состояния повреждён (%s), сохранён как %s. Начинаю заново.", error, broken.name)
            return State()

    def save(self, state: State) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(state.to_json(), encoding="utf-8")
        os.replace(tmp, self.path)


class DiskStateStore:
    def __init__(self, disk, path: str):
        self.disk = disk
        self.path = path

    def load(self) -> State:
        raw = self.disk.download(self.path)
        if raw is None:
            return State()
        try:
            return State.from_json(raw.decode("utf-8"))
        except (ValueError, TypeError, KeyError) as error:
            log.warning("Файл состояния на Диске повреждён (%s). Начинаю заново.", error)
            return State()

    def save(self, state: State) -> None:
        self.disk.upload(self.path, state.to_json().encode("utf-8"), overwrite=True)
