from mail2disk.config import Config
from mail2disk.runner import run_with_retries
from mail2disk.sync import Report


def config():
    return Config(email="me@yandex.ru", mail_password="mail", disk_password="disk")


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_transient_failure_is_retried(monkeypatch):
    reports = iter([Report(errors=["временно недоступен"], retryable=True), Report()])
    attempts = []
    clock = Clock()

    def run_once(_config):
        attempts.append(_config)
        return next(reports)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(
        config(),
        sleep=clock.sleep,
        clock=clock.monotonic,
        run_id="test-run",
    )

    assert report.ok
    assert len(attempts) == 2
    assert clock.sleeps == [60]


def test_auth_failure_is_not_retried(monkeypatch):
    attempts = []
    clock = Clock()

    def run_once(_config):
        attempts.append(_config)
        return Report(errors=["неверный пароль"], retryable=False)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(
        config(),
        sleep=clock.sleep,
        clock=clock.monotonic,
        run_id="test-run",
    )

    assert not report.ok
    assert len(attempts) == 1
    assert clock.sleeps == []


def test_transient_failure_repeats_every_fifteen_minutes_until_window_ends(monkeypatch):
    attempts = []
    clock = Clock()

    def run_once(_config):
        attempts.append(_config)
        return Report(errors=["временно недоступен"], retryable=True)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(
        config(),
        sleep=clock.sleep,
        clock=clock.monotonic,
        run_id="test-run",
    )

    assert not report.ok
    assert len(attempts) == 14
    assert clock.sleeps == [60, 300] + [900] * 11


def test_time_spent_in_attempts_counts_toward_retry_window(monkeypatch):
    attempts = []
    clock = Clock()

    def run_once(_config):
        attempts.append(_config)
        clock.now += 60 * 60
        return Report(errors=["временно недоступен"], retryable=True)

    monkeypatch.setattr("mail2disk.runner.run_once", run_once)

    report = run_with_retries(
        config(),
        sleep=clock.sleep,
        clock=clock.monotonic,
        run_id="test-run",
    )

    assert not report.ok
    assert len(attempts) == 3
    assert clock.sleeps == [60, 300]
