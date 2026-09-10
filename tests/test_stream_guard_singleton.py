# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The singleton check must not mistake a recycled PID for another guard.

Found on a live machine: `arctis-stream-guard` had been dead for four days,
"failed (Result: start-limit-hit)" with a main process that exited 0. The PID
file was written at 11:20:08 and the machine booted at 11:25:12 — the file had
outlived a reboot, and the number in it (1345) belonged to whatever early-boot
daemon the kernel had since handed it to. `os.kill(1345, 0)` succeeded, the
guard said "already running", exited 0, and systemd retried until it gave up.

Nothing about that reads as a failure: the unit exits successfully and the
desktop looks normal. It surfaces only as audio going out on a screen share
that the guard existed to keep off it, which is the worst way to find out.

These tests drive `_acquire_singleton` against a PID file in a temp HOME, with
/proc reads faked so a "live PID" can be made to be, or not be, a real guard.
"""
from __future__ import annotations

import os

import pytest

from arctis_sound_manager.scripts import stream_guard as sg


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    path = tmp_path / "stream_guard.pid"
    monkeypatch.setattr(sg, "_PID_FILE", path)
    return path


def _cmdlines(monkeypatch, table: dict[int, bytes]) -> None:
    """Fake /proc/<pid>/cmdline: absent from *table* means no such process."""
    def _read(pid: int) -> bool:
        return b"asm-stream-guard" in table.get(pid, b"")
    monkeypatch.setattr(sg, "_is_our_process", _read)


def test_takes_over_when_the_pid_was_recycled(pid_file, monkeypatch):
    """The reported bug: a live PID that is somebody else entirely."""
    pid_file.write_text("1345")
    _cmdlines(monkeypatch, {1345: b"/usr/lib/systemd/systemd-journald\0"})

    assert sg._acquire_singleton() is True
    assert pid_file.read_text() == str(os.getpid())


def test_takes_over_when_the_pid_is_gone(pid_file, monkeypatch):
    pid_file.write_text("1345")
    _cmdlines(monkeypatch, {})

    assert sg._acquire_singleton() is True
    assert pid_file.read_text() == str(os.getpid())


def test_defers_to_a_real_second_guard(pid_file, monkeypatch):
    """The check still has to do its actual job."""
    pid_file.write_text("4242")
    _cmdlines(monkeypatch, {4242: b"/usr/bin/python\0/usr/bin/asm-stream-guard\0"})

    assert sg._acquire_singleton() is False
    assert pid_file.read_text() == "4242"       # not stolen from the live one


def test_a_garbage_pid_file_is_taken_over(pid_file, monkeypatch):
    pid_file.write_text("not a pid")
    _cmdlines(monkeypatch, {})

    assert sg._acquire_singleton() is True
    assert pid_file.read_text() == str(os.getpid())


def test_no_pid_file_at_all(pid_file, monkeypatch):
    _cmdlines(monkeypatch, {})

    assert sg._acquire_singleton() is True
    assert pid_file.read_text() == str(os.getpid())


def test_our_own_pid_is_not_another_instance(pid_file, monkeypatch):
    """A file left naming *this* process must not stop it starting.

    Reachable after a crash-and-respawn that reuses the pid, and the check
    would otherwise be reading its own name and deferring to itself.
    """
    pid_file.write_text(str(os.getpid()))
    _cmdlines(monkeypatch, {os.getpid(): b"/usr/bin/asm-stream-guard\0"})

    assert sg._acquire_singleton() is True


def test_is_our_process_reads_the_command_line(monkeypatch, tmp_path):
    """The real /proc reader, against a file laid out the way /proc is."""
    proc = tmp_path / "proc"
    (proc / "10").mkdir(parents=True)
    (proc / "10" / "cmdline").write_bytes(b"/usr/bin/python\0/usr/bin/asm-stream-guard\0")
    (proc / "11").mkdir(parents=True)
    (proc / "11" / "cmdline").write_bytes(b"/usr/bin/pw-mon\0")

    real_path = sg.Path

    class _Path(type(real_path())):
        def __new__(cls, arg):
            return real_path(str(arg).replace("/proc/", f"{proc}/"))

    monkeypatch.setattr(sg, "Path", _Path)

    assert sg._is_our_process(10) is True
    assert sg._is_our_process(11) is False
    assert sg._is_our_process(12) is False      # no such process
