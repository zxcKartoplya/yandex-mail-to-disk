import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from . import APP_NAME
from .paths import app_dir

TASK_NAME = APP_NAME
LAUNCHD_LABEL = "ru.yandex-mail-to-disk.sync"


class SchedulerError(Exception):
    pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def installed_executable_path() -> Path:
    name = f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME
    return app_dir() / name


def install_executable() -> Path:
    current = Path(sys.executable).resolve()
    target = installed_executable_path()
    if current == target.resolve():
        return target
    tmp = target.with_suffix(".new")
    shutil.copyfile(current, tmp)
    os.chmod(tmp, 0o755)
    try:
        os.replace(tmp, target)
    except PermissionError as error:
        tmp.unlink(missing_ok=True)
        raise SchedulerError(
            f"Не удалось обновить {target}: файл занят. Закройте другие окна программы и попробуйте ещё раз."
        ) from error
    return target


def run_command() -> tuple[list[str], Path]:
    if is_frozen():
        executable = install_executable()
        return [str(executable), "--run"], executable.parent
    project_root = Path(__file__).resolve().parent.parent
    return [sys.executable, "-m", "mail2disk", "--run"], project_root


def windows_task_xml(command: list[str], working_dir: Path, hour: int, minute: int, user: str) -> str:
    start = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    arguments = " ".join(f'"{arg}"' if " " in arg else arg for arg in command[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape("Раз в день выгружает вложения из Яндекс Почты на Яндекс Диск")}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command[0])}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(working_dir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def launchd_plist(command: list[str], working_dir: Path, hour: int, minute: int, log_file: Path) -> bytes:
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": command,
        "WorkingDirectory": str(working_dir),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }
    return plistlib.dumps(data)


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


def _windows_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or os.getlogin()
    return f"{domain}\\{user}" if domain else user


def install(hour: int, minute: int) -> str:
    command, working_dir = run_command()
    if sys.platform == "win32":
        xml = windows_task_xml(command, working_dir, hour, minute, _windows_user())
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as handle:
            handle.write(xml)
            xml_path = handle.name
        try:
            result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"])
        finally:
            os.unlink(xml_path)
        if result.returncode != 0:
            raise SchedulerError(f"Не удалось добавить задачу в Планировщик Windows: {result.stderr or result.stdout}")
        return f"Автозапуск включён: каждый день в {hour:02d}:{minute:02d} (Планировщик заданий Windows, задача «{TASK_NAME}»)."
    if sys.platform == "darwin":
        plist_path = launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", domain, str(plist_path)])
        plist_path.write_bytes(launchd_plist(command, working_dir, hour, minute, app_dir() / "launchd.log"))
        result = _run(["launchctl", "bootstrap", domain, str(plist_path)])
        if result.returncode != 0:
            raise SchedulerError(f"Не удалось включить автозапуск (launchd): {result.stderr or result.stdout}")
        return f"Автозапуск включён: каждый день в {hour:02d}:{minute:02d} и при входе в систему."
    raise SchedulerError("Автозапуск умеет настраиваться только на Windows и macOS. Используйте cron.")


def uninstall() -> str:
    if sys.platform == "win32":
        result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        if result.returncode != 0 and is_installed():
            raise SchedulerError(f"Не удалось удалить задачу: {result.stderr or result.stdout}")
        return "Автозапуск отключён."
    if sys.platform == "darwin":
        plist_path = launchd_plist_path()
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)])
        plist_path.unlink(missing_ok=True)
        return "Автозапуск отключён."
    raise SchedulerError("Автозапуск умеет настраиваться только на Windows и macOS.")


def is_installed() -> bool:
    if sys.platform == "win32":
        return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
    if sys.platform == "darwin":
        return launchd_plist_path().exists()
    return False
