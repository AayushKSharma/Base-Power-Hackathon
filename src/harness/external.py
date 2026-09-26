"""Run a capacity policy in another process, over the JSON-lines protocol.

The child is untrusted. A reply that misses the time budget is a timeout: the
process is killed, so a late line cannot be read as the next decision, and the
harness reports the fallback capability instead. Killing it is not a restart.
A new process is started on the next decision.
"""

from __future__ import annotations

import math
import os
import select
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from typing import IO, Any

from harness.observation import Observation
from harness.policy import Capability, PolicyError
from harness.products import PRODUCTS
from harness.protocol import (
    decode, encode, for_policy, hello_message, observation_message, parse_capability, parse_hello,
)
from harness.scenario import ProductRules
from harness.scorecard import FaultCounts

FALLBACKS = ("last_good", "zero")


class ExternalPolicy:
    """A policy whose `decide` is one request to a child process.

    Started on the first decision of an operating day. `run` closes it when
    the range finishes; `begin_day` replaces the process without counting a
    restart. The context manager does the same close.
    """

    def __init__(self, command: Sequence[str], products: Mapping[str, ProductRules], *,
                 timeout_s: float = 1.0, fallback: str = "last_good"):
        if not command or not all(command):
            raise PolicyError(f"policy command is empty: {command!r}")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise PolicyError(f"decision timeout must be a positive number of seconds, got {timeout_s}")
        if fallback not in FALLBACKS:
            raise PolicyError(f"fallback must be {' or '.join(FALLBACKS)}, got {fallback!r}")
        self.command = list(command)
        self._products = products
        self._timeout_s = timeout_s
        self._fallback_mode = fallback
        self._name = " ".join(self.command)
        self._wants_per_home = False
        self._last_good: dict[str, float] | None = None
        self._faults = FaultCounts()
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: _LineReader | None = None
        self._stderr = b""
        self._stderr_thread: threading.Thread | None = None
        self._ready = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def faults(self) -> FaultCounts:
        """Cumulative counts. The runner subtracts a day's opening value."""
        return self._faults

    def begin_day(self) -> None:
        """Drop the child and the remembered decision. This is not a restart.

        Each operating day is simulated on its own, so a range run keeps matching
        the combination of its single-day runs.
        """
        self._stop()
        self._last_good = None

    def decide(self, observation: Observation) -> Capability:
        if not self._ready and not self._handshake():
            return self._fallback()
        try:
            self._write(observation_message(for_policy(observation, wants_per_home=self._wants_per_home)))
            message = self._read()
        except PolicyError:
            self._crashed()
            return self._fallback()
        if message is None:
            self._timed_out()
            return self._fallback()
        capability = parse_capability(message)
        if capability is None:
            # The line was consumed, so the next observation stays in step.
            self._add(malformed=1, fallbacks=1)
            return self._fallback()
        self._last_good = capability
        return capability

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self._reader = None
        self._ready = False
        if proc is None:
            return
        if proc.poll() is None and proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self._join_stderr()

    def __enter__(self) -> ExternalPolicy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _handshake(self) -> bool:
        self._spawn()
        try:
            self._write(hello_message(self._products))
            message = self._read()
        except PolicyError:
            self._crashed()
            return False
        if message is None:
            self._timed_out()
            return False
        parsed = parse_hello(message)
        if parsed is None:
            self._add(malformed=1, fallbacks=1)
            self._stop()
            return False
        self._name, _, self._wants_per_home = parsed
        self._ready = True
        return True

    def _fallback(self) -> dict[str, float]:
        if self._fallback_mode == "last_good" and self._last_good is not None:
            return dict(self._last_good)
        return {product: 0.0 for product in PRODUCTS}

    def _timed_out(self) -> None:
        self._add(timeouts=1, fallbacks=1)
        self._stop()

    def _crashed(self) -> None:
        """The child died on this decision. Count a restart and forget the process."""
        self._add(restarts=1, fallbacks=1)
        self._stop()

    def _add(self, **increments: int) -> None:
        self._faults = self._faults + FaultCounts(**increments)

    def _spawn(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise PolicyError(f"cannot start policy {self.command[0]!r}: {e.strerror}") from e
        if self._proc.stdout is None or self._proc.stderr is None:
            raise PolicyError("policy stdout was not piped")
        self._reader = _LineReader(self._proc.stdout)
        self._stderr = b""
        self._stderr_thread = threading.Thread(
            target=_collect_stderr, args=(self._proc.stderr, self), daemon=True,
        )
        self._stderr_thread.start()
        self._ready = False

    def _stop(self) -> None:
        """Kill the child. A timeout does this on purpose, so it is not a restart."""
        proc, self._proc = self._proc, None
        self._reader = None
        self._ready = False
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        self._join_stderr()

    def _join_stderr(self) -> None:
        thread, self._stderr_thread = self._stderr_thread, None
        if thread is not None:
            thread.join(timeout=1)

    def _failure(self, headline: str) -> PolicyError:
        detail = self._stderr.decode(errors="replace").strip()
        if detail:
            headline = f"{headline}: {detail}"
        return PolicyError(headline)

    def _write(self, message: Mapping[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise PolicyError("policy is not running")
        try:
            proc.stdin.write(encode(message))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise PolicyError(f"policy stopped reading: {e}") from e

    def _read(self) -> Any | None:
        """One JSON message, None when the time budget expires."""
        if self._reader is None:
            raise PolicyError("policy is not running")
        try:
            line = self._reader.readline(self._timeout_s)
        except EOFError:
            proc = self._proc
            code = None if proc is None else proc.poll()
            self._join_stderr()
            raise self._failure(f"policy closed stdout (exit {code})") from None
        if line is None:
            return None
        return decode(line)


def _collect_stderr(stream: IO[bytes], policy: ExternalPolicy) -> None:
    """Drain stderr so a chatty policy cannot fill the pipe, and keep the tail."""
    tail = b""
    while True:
        block = stream.read(4096)
        if not block:
            break
        tail = (tail + block)[-8000:]
    policy._stderr = tail


class _LineReader:
    """Bytes from a pipe, one line at a time, with a wall-clock deadline."""

    def __init__(self, stream: IO[bytes]):
        self._fd = stream.fileno()
        self._buf = b""

    def readline(self, timeout: float) -> bytes | None:
        """A line without its newline, None on timeout, EOFError if the pipe closes."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self._fd, 65536)
            if not chunk:
                raise EOFError
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line
