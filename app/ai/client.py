"""ModelClient: the one seam to a language model, with three adapters.

- ClaudeCLI: live. Runs `claude -p` (the subscription CLI) with all tools off, from an empty
  temporary folder, with CLAUDE_CODE_OAUTH_TOKEN in its environment. Never an API key.
- Recorded: replays stored answers ("cassettes"). Used only by the eval script.
- Unavailable: no token. Every call fails, and the callers turn that into "controle nodig".

The service picks ClaudeCLI when the token is set, else Unavailable. It never picks Recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

TOKEN_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"


@dataclass(frozen=True)
class TaskSpec:
    model: str
    timeout_s: int
    retries: int


# Intake reads a whole contract once: the stronger model, one retry. A line category is asked
# per invoice: the small fast model, no retry, so a slow model can never hold up a check.
TASKS: dict[str, TaskSpec] = {
    "contract_rules": TaskSpec(model="sonnet", timeout_s=60, retries=1),
    "requirement_rules": TaskSpec(model="sonnet", timeout_s=60, retries=1),
    "line_category": TaskSpec(model="haiku", timeout_s=20, retries=0),
}


class ModelError(Exception):
    """The model gave no usable answer (CLI error, timeout, no JSON, no recorded answer)."""


class ModelUnavailable(ModelError):
    """There is no model to ask: no token is set, or the token was refused."""


class ModelClient(Protocol):
    mode: str  # live | recorded | unavailable

    def complete_json(self, task: str, prompt: str) -> dict:
        """Ask the model for one JSON object. Raises ModelError when there is no usable answer."""
        ...


def parse_json(text: str) -> dict:
    """The JSON object in a reply; code fences and prose around it are dropped."""
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    candidate = fenced.group(1) if fenced else stripped
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in the reply")
        candidate = candidate[start : end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("the reply is JSON but not an object")
    return parsed


def _run_cli(prompt: str, model: str, timeout_s: int) -> str:
    """Run the CLI once and return the text of the reply. Tests replace this function."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    if not env.get(TOKEN_VARIABLE):
        raise ModelUnavailable("geen token")
    args = [
        shutil.which("claude") or "claude",
        "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    with tempfile.TemporaryDirectory(prefix="factuurcontrole-claude-") as cwd:
        try:
            proc = subprocess.run(
                args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                cwd=cwd, env=env, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModelError(f"het model antwoordde niet binnen {timeout_s} s") from exc
        except OSError as exc:
            raise ModelError(f"de claude CLI start niet: {exc}") from exc
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ModelError(f"claude stopte met code {proc.returncode}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ModelError("claude gaf geen JSON-envelop terug") from exc
    text = str(envelope.get("result", ""))
    if envelope.get("is_error"):
        if envelope.get("api_error_status") in (401, 403) or "authenticat" in text.lower():
            raise ModelUnavailable("token geweigerd")
        raise ModelError("claude meldde een fout")
    return text


class ClaudeCLI:
    mode = "live"

    def __init__(self, run: Callable[[str, str, int], str] | None = None):
        self._run = run or _run_cli

    def complete_json(self, task: str, prompt: str) -> dict:
        spec = TASKS[task]
        attempt_prompt = prompt
        for attempt in range(spec.retries + 1):
            try:
                return parse_json(self._run(attempt_prompt, spec.model, spec.timeout_s))
            except ModelUnavailable:
                raise
            except (ModelError, ValueError) as exc:
                if attempt == spec.retries:
                    raise ModelError(str(exc)) from exc
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply could not be used ({exc}). "
                    "Reply with the JSON object only, nothing before or after it."
                )
        raise AssertionError("unreachable")


def cassette_key(task: str, prompt: str) -> str:
    return hashlib.sha256(f"{task}\n{prompt}".encode("utf-8")).hexdigest()


class Recorded:
    """Replays answers recorded from a live run. The key is sha256(task + masked prompt)."""

    mode = "recorded"

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, task: str, prompt: str) -> Path:
        return self.directory / f"{task}-{cassette_key(task, prompt)[:16]}.json"

    def complete_json(self, task: str, prompt: str) -> dict:
        path = self._path(task, prompt)
        if not path.is_file():
            raise ModelError("geen opgenomen antwoord voor deze prompt")
        cassette = json.loads(path.read_text(encoding="utf-8"))
        if cassette["key"] != cassette_key(task, prompt):
            raise ModelError("geen opgenomen antwoord voor deze prompt")
        return cassette["response"]

    def write(self, task: str, prompt: str, response: dict) -> None:
        """Store one live answer. The prompt is kept next to it: it shows what the model saw."""
        self.directory.mkdir(parents=True, exist_ok=True)
        cassette = {
            "key": cassette_key(task, prompt),
            "task": task,
            "model": TASKS[task].model,
            "prompt": prompt,
            "response": response,
        }
        self._path(task, prompt).write_text(
            json.dumps(cassette, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


class Unavailable:
    mode = "unavailable"

    def complete_json(self, task: str, prompt: str) -> dict:
        raise ModelUnavailable("geen token")


def client_from_env() -> ModelClient:
    """Live Claude when the token is set; otherwise no model at all."""
    return ClaudeCLI() if os.environ.get(TOKEN_VARIABLE) else Unavailable()
