import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = (15, 600)
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
DAV_NS = {"d": "DAV:"}


class DiskError(Exception):
    pass


class DiskAuthError(DiskError):
    pass


@dataclass
class RemoteFile:
    size: int | None
    md5: str | None


def make_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=2,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _fail(response, action: str) -> DiskError:
    status = response.status_code
    if status in (401, 403):
        return DiskAuthError(
            "Яндекс Диск не принял пароль приложения (или токен). Проверьте его в настройках программы."
        )
    if status == 507:
        return DiskError("На Яндекс Диске закончилось место.")
    if status == 413:
        return DiskError(f"{action}: файл слишком большой для Яндекс Диска.")
    return DiskError(f"{action}: Яндекс Диск ответил {status}: {response.text[:300]}")


class BaseDisk:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or make_session()
        self._known_folders: set[str] = set()

    def _request(self, method: str, url: str, action: str, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            return self.session.request(method, url, **kwargs)
        except requests.RequestException as error:
            raise DiskError(f"{action}: нет связи с Яндекс Диском ({error}).") from error

    def ensure_folder(self, path: str) -> None:
        current = ""
        for segment in path.strip("/").split("/"):
            current = f"{current}/{segment}" if current else segment
            if current in self._known_folders:
                continue
            self.make_folder(current)
            self._known_folders.add(current)

    def check(self) -> None:
        raise NotImplementedError

    def make_folder(self, path: str) -> None:
        raise NotImplementedError

    def info(self, path: str) -> RemoteFile | None:
        raise NotImplementedError

    def upload(self, path: str, data: bytes, overwrite: bool = False) -> None:
        raise NotImplementedError

    def download(self, path: str) -> bytes | None:
        raise NotImplementedError


class WebDavDisk(BaseDisk):
    BASE_URL = "https://webdav.yandex.ru"

    def __init__(self, login: str, password: str, session: requests.Session | None = None):
        super().__init__(session)
        self.session.auth = (login, password)

    def _url(self, path: str) -> str:
        return f"{self.BASE_URL}/{quote(path.strip('/'), safe='/')}"

    def check(self) -> None:
        response = self._request("PROPFIND", self._url(""), "Проверка Диска", headers={"Depth": "0"})
        if response.status_code != 207:
            raise _fail(response, "Проверка Диска")

    def make_folder(self, path: str) -> None:
        response = self._request("MKCOL", self._url(path), f"Создание папки {path}")
        if response.status_code not in (201, 405):
            raise _fail(response, f"Создание папки {path}")

    def info(self, path: str) -> RemoteFile | None:
        action = f"Проверка файла {path}"
        response = self._request("PROPFIND", self._url(path), action, headers={"Depth": "0"})
        if response.status_code == 404:
            return None
        if response.status_code != 207:
            raise _fail(response, action)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as error:
            raise DiskError(f"{action}: непонятный ответ Диска.") from error
        size = root.findtext(".//d:getcontentlength", namespaces=DAV_NS)
        etag = (root.findtext(".//d:getetag", namespaces=DAV_NS) or "").strip('"').lower()
        return RemoteFile(
            size=int(size) if size and size.isdigit() else None,
            md5=etag if MD5_RE.match(etag) else None,
        )

    def upload(self, path: str, data: bytes, overwrite: bool = False) -> None:
        action = f"Загрузка {path}"
        headers = {"Content-Type": "application/octet-stream"}
        response = self._request("PUT", self._url(path), action, data=data, headers=headers)
        if response.status_code not in (200, 201, 204):
            raise _fail(response, action)

    def download(self, path: str) -> bytes | None:
        action = f"Чтение {path}"
        response = self._request("GET", self._url(path), action)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise _fail(response, action)
        return response.content


class RestDisk(BaseDisk):
    API_URL = "https://cloud-api.yandex.net/v1/disk"

    def __init__(self, token: str, session: requests.Session | None = None):
        super().__init__(session)
        self.auth_headers = {"Authorization": f"OAuth {token}"}

    @staticmethod
    def _path(path: str) -> str:
        return "disk:/" + path.strip("/")

    def _api(self, method: str, endpoint: str, action: str, **kwargs):
        return self._request(method, self.API_URL + endpoint, action, headers=self.auth_headers, **kwargs)

    def check(self) -> None:
        response = self._api("GET", "", "Проверка Диска", params={"fields": "total_space"})
        if response.status_code != 200:
            raise _fail(response, "Проверка Диска")

    def make_folder(self, path: str) -> None:
        response = self._api("PUT", "/resources", f"Создание папки {path}", params={"path": self._path(path)})
        if response.status_code not in (201, 409):
            raise _fail(response, f"Создание папки {path}")

    def info(self, path: str) -> RemoteFile | None:
        action = f"Проверка файла {path}"
        params = {"path": self._path(path), "fields": "size,md5,type"}
        response = self._api("GET", "/resources", action, params=params)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise _fail(response, action)
        data = response.json()
        md5 = (data.get("md5") or "").lower()
        return RemoteFile(size=data.get("size"), md5=md5 if MD5_RE.match(md5) else None)

    def upload(self, path: str, data: bytes, overwrite: bool = False) -> None:
        action = f"Загрузка {path}"
        params = {"path": self._path(path), "overwrite": "true" if overwrite else "false"}
        response = self._api("GET", "/resources/upload", action, params=params)
        if response.status_code != 200:
            raise _fail(response, action)
        href = response.json().get("href")
        if not href:
            raise DiskError(f"{action}: Диск не выдал адрес для загрузки.")
        uploaded = self._request("PUT", href, action, data=data)
        if uploaded.status_code not in (201, 202):
            raise _fail(uploaded, action)

    def download(self, path: str) -> bytes | None:
        action = f"Чтение {path}"
        response = self._api("GET", "/resources/download", action, params={"path": self._path(path)})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise _fail(response, action)
        href = response.json().get("href")
        if not href:
            raise DiskError(f"{action}: Диск не выдал адрес для скачивания.")
        content = self._request("GET", href, action)
        if content.status_code != 200:
            raise _fail(content, action)
        return content.content


def make_disk(config) -> BaseDisk:
    if config.disk_mode == "token":
        return RestDisk(config.disk_token)
    return WebDavDisk(config.email, config.disk_password)
