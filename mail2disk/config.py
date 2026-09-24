import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ENV_PREFIX = "MAIL2DISK_"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    email: str
    mail_password: str
    disk_password: str = ""
    disk_token: str = ""
    disk_folder: str = "Вложения из почты"
    imap_server: str = "imap.yandex.ru"
    include_folders: list[str] = field(default_factory=list)
    first_run_days: int = 7
    run_time: str = "09:00"
    min_inline_image_kb: int = 20

    @property
    def disk_mode(self) -> str:
        return "token" if self.disk_token else "webdav"

    @property
    def run_hour_minute(self) -> tuple[int, int]:
        match = TIME_RE.match(self.run_time)
        if match is None:
            raise ConfigError("Время запуска должно быть в формате ЧЧ:ММ.")
        return int(match.group(1)), int(match.group(2))

    def validate(self) -> None:
        problems = []
        if "@" not in self.email:
            problems.append("не указан адрес почты")
        if not self.mail_password:
            problems.append("не указан пароль приложения для Почты")
        if not self.disk_password and not self.disk_token:
            problems.append("не указан пароль приложения для Диска")
        if not self.disk_folder.strip("/ "):
            problems.append("не указана папка на Диске")
        if not TIME_RE.match(self.run_time):
            problems.append("время запуска должно быть в формате ЧЧ:ММ")
        if self.first_run_days < 0:
            problems.append("число дней для первой выгрузки не может быть отрицательным")
        if problems:
            raise ConfigError("Ошибка в настройках: " + "; ".join(problems) + ".")

    def normalized(self) -> "Config":
        data = asdict(self)
        data["email"] = self.email.strip()
        data["mail_password"] = self.mail_password.replace(" ", "")
        data["disk_password"] = self.disk_password.replace(" ", "")
        data["disk_token"] = self.disk_token.strip()
        data["disk_folder"] = self.disk_folder.strip().strip("/")
        return Config(**data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in data.items() if k in known})
        return config.normalized()

    @classmethod
    def from_env(cls, environ=None) -> "Config":
        environ = os.environ if environ is None else environ
        data = {}
        for f in fields(cls):
            raw = environ.get(ENV_PREFIX + f.name.upper())
            if raw is None or raw == "":
                continue
            if f.name == "include_folders":
                data[f.name] = [part.strip() for part in raw.split(",") if part.strip()]
            elif f.name in ("first_run_days", "min_inline_image_kb"):
                data[f.name] = int(raw)
            else:
                data[f.name] = raw
        data.setdefault("email", "")
        data.setdefault("mail_password", "")
        return cls.from_dict(data)


def load_config(path: Path) -> Config | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"Не удалось прочитать файл настроек {path}: {error}") from error
    return Config.from_dict(data)


def save_config(config: Config, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_env_file(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result
