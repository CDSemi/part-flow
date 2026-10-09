"""Small shared helpers: list-argv subprocesses (never a shell string), the inner Docker CLI, atomic writes."""
import json
import os
from pathlib import Path
import subprocess
import time

DOCKER = "/usr/local/bin/docker"
BASE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
            "LANG": "C.UTF-8", "DOCKER_HOST": "unix:///var/run/docker.sock"}


class HarnessError(Exception):
    """A harness failure. A message starting with 'BLOCKED:' is an environment/fixture block (SPEC 1.4)."""


class Completed:
    def __init__(self, argv, returncode, stdout, stderr, started, ended):
        self.argv, self.returncode, self.stdout, self.stderr = argv, returncode, stdout, stderr
        self.started, self.ended = started, ended


def utc(timestamp=None):
    value = time.time() if timestamp is None else timestamp
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(value)) + ".%03dZ" % int((value % 1) * 1000)


def run(argv, *, env=None, input_bytes=None, timeout=None, binary=False, cwd=None):
    environment = dict(BASE_ENV)
    if env:
        environment.update(env)
    started = utc()
    try:
        result = subprocess.run(list(argv), input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, timeout=timeout, check=False, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        err = (exc.stderr or b"") + b"\n[harness: timed out]"
        return Completed(list(argv), 124, out if binary else out.decode("utf-8", "replace"),
                         err.decode("utf-8", "replace"), started, utc())
    stdout = result.stdout if binary else result.stdout.decode("utf-8", "replace")
    return Completed(list(argv), result.returncode, stdout, result.stderr.decode("utf-8", "replace"), started, utc())


def docker(argv, *, timeout=600, input_bytes=None, binary=False):
    return run([DOCKER, *argv], timeout=timeout, input_bytes=input_bytes, binary=binary)


def docker_checked(argv, *, timeout=600, input_bytes=None):
    result = docker(argv, timeout=timeout, input_bytes=input_bytes)
    if result.returncode != 0:
        raise HarnessError(f"docker {' '.join(argv[:4])} failed ({result.returncode}): {result.stderr.strip()[:500]}")
    return result.stdout


def docker_json(argv, *, timeout=600):
    text = docker_checked(argv, timeout=timeout)
    return json.loads(text) if text.strip() else None


def write_private(path, text, *, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    data = text.encode("utf-8") if isinstance(text, str) else text
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(str(temp), mode)
    os.replace(str(temp), str(path))


def wait_until(predicate, *, timeout, interval=0.5, what="condition"):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise HarnessError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(interval)
