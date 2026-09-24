import plistlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mail2disk.config import Config, ConfigError, load_config, load_env_file, save_config
from mail2disk.scheduler import launchd_plist, windows_task_xml
from mail2disk.state import FileStateStore, FolderState, State

TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_windows_task_xml_is_valid_and_daily():
    xml = windows_task_xml(
        [r"C:\Users\Иван Петров\AppData\Roaming\YandexMailToDisk\YandexMailToDisk.exe", "--run"],
        Path(r"C:\Users\Иван Петров\AppData\Roaming\YandexMailToDisk"),
        9,
        30,
        r"PC\Иван Петров",
    )
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    assert root.findtext(".//t:ScheduleByDay/t:DaysInterval", namespaces=TASK_NS) == "1"
    assert root.findtext(".//t:StartBoundary", namespaces=TASK_NS).endswith("T09:30:00")
    assert root.findtext(".//t:StartWhenAvailable", namespaces=TASK_NS) == "true"
    assert root.findtext(".//t:DisallowStartIfOnBatteries", namespaces=TASK_NS) == "false"
    assert root.findtext(".//t:Command", namespaces=TASK_NS).endswith("YandexMailToDisk.exe")
    assert root.findtext(".//t:Arguments", namespaces=TASK_NS) == "--run"
    assert root.findtext(".//t:UserId", namespaces=TASK_NS) == r"PC\Иван Петров"


def test_launchd_plist():
    data = plistlib.loads(launchd_plist(["/x/YandexMailToDisk", "--run"], Path("/x"), 7, 5, Path("/x/l.log")))
    assert data["ProgramArguments"] == ["/x/YandexMailToDisk", "--run"]
    assert data["StartCalendarInterval"] == {"Hour": 7, "Minute": 5}
    assert data["RunAtLoad"] is True


def test_config_roundtrip_and_normalization(tmp_path):
    config = Config.from_dict(
        {"email": " me@yandex.ru ", "mail_password": "abcd efgh", "disk_password": "x y", "disk_folder": "/Папка/", "junk": 1}
    )
    assert config.mail_password == "abcdefgh"
    assert config.disk_folder == "Папка"
    path = tmp_path / "config.json"
    save_config(config, path)
    assert load_config(path) == config
    if sys.platform != "win32":
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_config_validation():
    with pytest.raises(ConfigError, match="пароль приложения для Диска"):
        Config(email="me@yandex.ru", mail_password="x").validate()
    with pytest.raises(ConfigError, match="ЧЧ:ММ"):
        Config(email="me@yandex.ru", mail_password="x", disk_password="y", run_time="25:00").validate()
    Config(email="me@yandex.ru", mail_password="x", disk_token="t").validate()


def test_config_from_env_file(tmp_path):
    env = tmp_path / "test.env"
    env.write_text(
        "# comment\nMAIL2DISK_EMAIL=me@yandex.ru\nMAIL2DISK_MAIL_PASSWORD='p1'\n"
        "MAIL2DISK_DISK_PASSWORD=p2\nMAIL2DISK_DISK_TOKEN=\nMAIL2DISK_FIRST_RUN_DAYS=3\n",
        encoding="utf-8",
    )
    config = Config.from_env(load_env_file(env))
    assert (config.email, config.mail_password, config.disk_password, config.first_run_days) == ("me@yandex.ru", "p1", "p2", 3)
    assert config.disk_mode == "webdav"


def test_file_state_store_roundtrip_and_corruption(tmp_path):
    store = FileStateStore(tmp_path / "state.json")
    assert store.load() == State()
    state = State(folders={"INBOX": FolderState(5, 42)}, failures={"INBOX|5|43": 1}, last_success="2026-09-24T09:00:00")
    store.save(state)
    assert store.load() == state
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    assert store.load() == State()
    assert (tmp_path / "state.broken.json").exists()
