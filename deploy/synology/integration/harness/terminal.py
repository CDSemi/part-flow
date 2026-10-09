r"""Scripted terminal (SPEC 6.2): one command with a pty as stdin and pipes for stdout/stderr.

A prompt is the unterminated tail of stdout (``input()`` flushes it before reading). The step's dialogue decides
the answer:

- ``Type exactly '<phrase>'`` prompts: the phrase is typed only if it matches one of the step's phrase regexes
  (``phrases``); any other phrase aborts the step (SIGTERM) and fails it;
- every other prompt must match one (regex -> answer) pair of ``dialogue``, searched in the stdout window that ends
  at the prompt (patterns end with ``\Z``); an unmatched prompt aborts the step the same way.

``on_tick(process)`` runs every ``tick`` seconds while the command runs (fault injection, observation). The result
records argv, the exact typed answers, the exit status and both streams.
"""
import os
import re
import selectors
import signal
import subprocess
import termios
import time

PHRASE_PROMPT_RE = re.compile(r"Type exactly '(?P<phrase>[^']*)'(?: to continue \(q cancels\))?: \Z")
PROMPT_TAIL_RE = re.compile(r"(?:: |\? |\]: |> )\Z")
QUIET_PROMPT_SECONDS = 0.6
WINDOW = 4000  # dialogue patterns see the stdout window ending at the prompt (``\Z``-anchored), so a question's
               # preceding label lines can tell two identical prompt lines apart


class StepAborted(Exception):
    pass


class Result:
    def __init__(self, argv):
        self.argv = list(argv)
        self.typed = []
        self.prompts = []
        self.stdout = ""
        self.stderr = ""
        self.exit = None
        self.aborted = None
        self.started_at = None
        self.ended_at = None
        self.pid = None
        self.timed_out = False

    def as_record(self):
        return {"argv": self.argv, "typed": list(self.typed), "prompts": list(self.prompts), "exit": self.exit,
                "aborted": self.aborted, "timed_out": self.timed_out, "started_at": self.started_at,
                "ended_at": self.ended_at}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _quiet_pty():
    master, slave = os.openpty()
    attributes = termios.tcgetattr(slave)
    attributes[3] &= ~(termios.ECHO | termios.ECHONL)  # lflag: the typed answers are recorded, not echoed
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    return master, slave


def run(argv, *, phrases=(), dialogue=(), env=None, cwd=None, timeout=3600, on_tick=None, tick=0.02,
        max_answers=64, on_start=None):
    """Run ``argv`` at a scripted terminal. ``phrases``: regexes a typed confirmation phrase must fully match.
    ``dialogue``: [(prompt regex, answer)] tried in order (each pair may answer many times). Returns Result."""
    result = Result(argv)
    phrase_res = [re.compile(item) for item in phrases]
    dialogue_res = [(re.compile(pattern), answer) for pattern, answer in dialogue]
    master, slave = _quiet_pty()
    environment = env if env is not None else {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "dumb",
                                               "LANG": "C.UTF-8"}
    result.started_at = _utc()
    process = subprocess.Popen(list(argv), stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                               cwd=cwd, start_new_session=True, close_fds=True)
    os.close(slave)
    result.pid = process.pid
    if on_start is not None:
        on_start(process)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks = {"stdout": [], "stderr": []}
    tail = ""
    window = []          # stdout with a newline after every answered prompt (the typed answer is not echoed)
    pending = False      # the current tail is new output, not yet treated as a prompt
    last_prompt = None
    last_output = time.monotonic()
    deadline = time.monotonic() + timeout
    open_streams = 2
    answers = 0

    aborted_at = [None]
    exited_at = None

    def abort(reason):
        result.aborted = reason
        aborted_at[0] = time.monotonic()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    try:
        while True:
            if process.poll() is not None:
                # An orphaned grandchild may still hold a stream open: never wait on it past the command's exit.
                exited_at = exited_at or time.monotonic()
                if not open_streams or time.monotonic() - exited_at > 2.0:
                    break
            if aborted_at[0] is not None and time.monotonic() - aborted_at[0] > 30 and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not open_streams:
                time.sleep(tick)
            for key, _ in selector.select(timeout=tick):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    open_streams -= 1
                    continue
                text = data.decode("utf-8", "replace")
                chunks[key.data].append(text)
                last_output = time.monotonic()
                if key.data == "stdout":
                    window.append(text)
                    tail = (tail + text).rsplit("\n", 1)[-1]
                    pending = True
            if on_tick is not None:
                on_tick(process)
            now = time.monotonic()
            if now > deadline and result.aborted is None:
                result.timed_out = True
                abort("timeout")
            if tail and pending and result.aborted is None and process.poll() is None:
                match = PHRASE_PROMPT_RE.search(tail)
                quiet = now - last_output >= QUIET_PROMPT_SECONDS
                if match or (quiet and PROMPT_TAIL_RE.search(tail)):
                    result.prompts.append(tail)
                    answer = None
                    if match:
                        phrase = match.group("phrase")
                        if any(item.fullmatch(phrase) for item in phrase_res):
                            answer = phrase
                        else:
                            abort("phrase-not-allowed: " + phrase)
                    else:
                        text_window = "".join(window)[-WINDOW:]
                        for pattern, reply in dialogue_res:
                            if pattern.search(text_window):
                                answer = reply
                                break
                        if answer is None:
                            abort("unexpected-prompt: " + tail)
                    if answer is not None:
                        answers += 1
                        if answers > max_answers:
                            abort("too-many-answers")
                        elif last_prompt == (tail, answer):
                            abort("repeated-prompt: " + tail)  # the same answer was refused; never loop on it
                        else:
                            os.write(master, (answer + "\n").encode("utf-8"))
                            result.typed.append(answer)
                            last_prompt = (tail, answer)
                    window.append("\n")
                    pending = False
                    tail = ""
        result.exit = process.wait(timeout=max(1.0, deadline - time.monotonic() + 60))
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            result.exit = process.returncode
        selector.close()
        os.close(master)
    result.stdout = "".join(chunks["stdout"])
    result.stderr = "".join(chunks["stderr"])
    result.ended_at = _utc()
    return result
