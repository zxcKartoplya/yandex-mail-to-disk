from mail2disk.config import Config
from mail2disk.runner import run_with_retries
from mail2disk.sync import Report


def config():
    return Config(email="me@yandex.ru", mail_password="mail", disk_password="disk")


def test_transient_failure_is_retried(monkeypatch):
    reports = iter([Report(errors=["временно недоступен"], retryable=True), Report()])
    attempts = []
    sleeps = []

    def run_once(_config):
        attempts.append(_config)
        return next(reports)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(config(), retry_delays=(60, 300), sleep=sleeps.append)

    assert report.ok
    assert len(attempts) == 2
    assert sleeps == [60]


def test_auth_failure_is_not_retried(monkeypatch):
    attempts = []
    sleeps = []

    def run_once(_config):
        attempts.append(_config)
        return Report(errors=["неверный пароль"], retryable=False)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(config(), retry_delays=(60, 300), sleep=sleeps.append)

    assert not report.ok
    assert len(attempts) == 1
    assert sleeps == []


def test_transient_failure_stops_after_retry_limit(monkeypatch):
    attempts = []
    sleeps = []

    def run_once(_config):
        attempts.append(_config)
        return Report(errors=["временно недоступен"], retryable=True)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(config(), retry_delays=(60, 300), sleep=sleeps.append)

    assert not report.ok
    assert len(attempts) == 3
    assert sleeps == [60, 300]
