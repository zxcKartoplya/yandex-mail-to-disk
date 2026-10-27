import io
import plistlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mail2disk import cli, scheduler
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
    state = State(
        folders={"INBOX": FolderState(5, 42)},
        failures={"INBOX|5|43": 1},
        last_success="2026-09-24T09:00:00",
        last_attempt="2026-09-25T09:00:00",
        last_error="Яндекс Диск временно недоступен",
    )
    store.save(state)
    assert store.load() == state
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    assert store.load() == State()
    assert (tmp_path / "state.broken.json").exists()


def test_old_state_without_attempt_fields_is_supported():
    state = State.from_json('{"folders": {}, "failures": {}, "last_success": "2026-09-24T09:00:00"}')

    assert state.last_attempt is None
    assert state.last_error is None


def test_status_shows_last_background_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "state.json"
    FileStateStore(path).save(
        State(
            last_success="2026-10-08T09:00:00",
            last_attempt="2026-10-27T09:00:00",
            last_error="Создание папки: Яндекс Диск временно недоступен (код 503).",
        )
    )
    monkeypatch.setattr(cli, "state_path", lambda: path)
    monkeypatch.setattr(cli, "log_path", lambda: tmp_path / "log.txt")
    monkeypatch.setattr(cli, "is_installed", lambda: True)

    cli.print_status(Config(email="me@yandex.ru", mail_password="mail", disk_password="disk"))

    output = capsys.readouterr().out
    assert "Последняя попытка: 2026-10-27 09:00:00" in output
    assert "Последняя успешная выгрузка: 2026-10-08 09:00:00" in output
    assert "Яндекс Диск временно недоступен (код 503)" in output


def test_external_frozen_windows_executable_needs_update(tmp_path, monkeypatch):
    installed = tmp_path / "YandexMailToDisk.exe"
    monkeypatch.setattr(scheduler.sys, "platform", "win32")
    monkeypatch.setattr(scheduler.sys, "executable", str(tmp_path / "YandexMailToDisk-Installer.exe"))
    monkeypatch.setattr(scheduler, "is_frozen", lambda: True)
    monkeypatch.setattr(scheduler, "installed_executable_path", lambda: installed)

    assert scheduler.needs_windows_install_update()


def test_installed_windows_executable_does_not_update_itself(tmp_path, monkeypatch):
    installed = tmp_path / "YandexMailToDisk.exe"
    monkeypatch.setattr(scheduler.sys, "platform", "win32")
    monkeypatch.setattr(scheduler.sys, "executable", str(installed))
    monkeypatch.setattr(scheduler, "is_frozen", lambda: True)
    monkeypatch.setattr(scheduler, "installed_executable_path", lambda: installed)

    assert not scheduler.needs_windows_install_update()


def test_existing_install_updates_without_changing_config(monkeypatch, capsys):
    config = Config(email="me@yandex.ru", mail_password="mail", disk_password="disk", run_time="07:25")
    calls = []
    monkeypatch.setattr(cli, "needs_windows_install_update", lambda: True)
    monkeypatch.setattr(cli, "install", lambda hour, minute: calls.append((hour, minute)))

    assert cli.update_existing_install(config) == cli.EXIT_OK
    assert calls == [(7, 25)]
    assert config.mail_password == "mail"
    assert config.disk_password == "disk"
    output = capsys.readouterr().out
    assert "Программа обновлена до версии 1.1.0" in output
    assert "Настройки, журнал и история обработанных писем сохранены" in output


def test_first_interactive_run_still_opens_setup(monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda console=True: None)
    monkeypatch.setattr(cli, "load_config", lambda path: None)
    monkeypatch.setattr(cli, "interactive_menu", lambda: 17)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        cli,
        "update_existing_install",
        lambda config: pytest.fail("update must not run before first setup"),
    )

    assert cli.main([]) == 17
