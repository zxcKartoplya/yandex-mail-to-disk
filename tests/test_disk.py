import pytest
import requests

from mail2disk.disk import DiskAuthError, DiskError, RestDisk, WebDavDisk

PROPFIND_FILE = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:"><d:response><d:href>/a/b.pdf</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status>
<d:prop><d:getcontentlength>3</d:getcontentlength><d:getetag>"0CC175B9C0F1B6A831C399E269772661"</d:getetag></d:prop>
</d:propstat></d:response></d:multistatus>"""


class FakeResponse:
    def __init__(self, status_code, content=b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self.text = content.decode(errors="replace")
        self._json = json_data

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.auth = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def webdav(*responses):
    session = FakeSession(responses)
    return WebDavDisk("me@yandex.ru", "pw", session=session), session


def rest(*responses):
    session = FakeSession(responses)
    return RestDisk("TOKEN", session=session), session


def test_webdav_url_encodes_cyrillic_and_spaces():
    disk, session = webdav(FakeResponse(201))
    disk.make_folder("Вложения из почты/2026-09-24")
    method, url, _ = session.calls[0]
    assert method == "MKCOL"
    assert url == "https://webdav.yandex.ru/%D0%92%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F%20%D0%B8%D0%B7%20%D0%BF%D0%BE%D1%87%D1%82%D1%8B/2026-09-24"
    assert session.auth == ("me@yandex.ru", "pw")


def test_webdav_ensure_folder_creates_each_level_once():
    disk, session = webdav(FakeResponse(201), FakeResponse(405), FakeResponse(201))
    disk.ensure_folder("a/b")
    disk.ensure_folder("a/b")
    disk.ensure_folder("a/c")
    assert [call[1].rsplit("/", 1)[-1] for call in session.calls] == ["a", "b", "c"]


def test_webdav_info_parses_size_and_md5():
    disk, _ = webdav(FakeResponse(207, PROPFIND_FILE))
    info = disk.info("a/b.pdf")
    assert info is not None
    assert info.size == 3
    assert info.md5 == "0cc175b9c0f1b6a831c399e269772661"


def test_webdav_info_missing_file():
    disk, _ = webdav(FakeResponse(404))
    assert disk.info("a/b.pdf") is None


def test_webdav_upload_ok_and_auth_error():
    disk, session = webdav(FakeResponse(201), FakeResponse(401))
    disk.upload("a/b.pdf", b"abc")
    assert session.calls[0][0] == "PUT"
    assert session.calls[0][2]["timeout"] == (15, 600)
    with pytest.raises(DiskAuthError):
        disk.upload("a/c.pdf", b"abc")


def test_webdav_no_space():
    disk, _ = webdav(FakeResponse(507))
    with pytest.raises(DiskError, match="закончилось место"):
        disk.upload("a/b.pdf", b"abc")


def test_network_error_becomes_disk_error():
    disk, _ = webdav(requests.ConnectionError("boom"))
    with pytest.raises(DiskError, match="нет связи"):
        disk.check()


def test_rest_upload_two_steps_without_token_on_upload_host():
    disk, session = rest(FakeResponse(200, json_data={"href": "https://uploader/x"}), FakeResponse(202))
    disk.upload("Папка/файл.pdf", b"abc")
    first, second = session.calls
    assert first[2]["params"] == {"path": "disk:/Папка/файл.pdf", "overwrite": "false"}
    assert first[2]["headers"] == {"Authorization": "OAuth TOKEN"}
    assert second[1] == "https://uploader/x"
    assert "headers" not in second[2]


def test_rest_make_folder_existing_is_ok():
    disk, _ = rest(FakeResponse(409))
    disk.make_folder("a")


def test_rest_info():
    disk, _ = rest(FakeResponse(200, json_data={"size": 3, "md5": "0CC175B9C0F1B6A831C399E269772661"}), FakeResponse(404))
    info = disk.info("a")
    assert info is not None and info.md5 == "0cc175b9c0f1b6a831c399e269772661"
    assert disk.info("b") is None


def test_rest_download():
    disk, _ = rest(FakeResponse(200, json_data={"href": "https://dl/x"}), FakeResponse(200, b"{}"))
    assert disk.download("state.json") == b"{}"
