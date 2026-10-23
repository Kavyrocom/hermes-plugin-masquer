"""Hermes plugin: /masquer, auto filter plus on-demand masking."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home

from . import masker

_NOTICE = (
    "Privacy: bracket tokens EMAIL, PHONE, IBAN, API_KEY, ADDRESS, NOM plus a number "
    "are placeholders for private values. Use them verbatim in tool calls; they "
    "are restored before tools run."
)
_HELP = (
    "/masquer status  : état du mode auto\n"
    "/masquer on  : active le mode auto\n"
    "/masquer off  : coupe le mode auto\n"
    "/masquer ajouter Marie Dupont  : ajoute un nom à masquer\n"
    "/masquer retirer Marie Dupont  : l'enlève\n"
    "/masquer liste  : montre les noms ajoutés\n"
    "/masquer ...  : masque un texte collé, ou un fichier"
)
_CHAT_LIMIT = 1500
_MAX_FILE = 5_000_000


def _session_id(**kwargs: Any) -> str:
    return str(kwargs.get("session_id") or "")


def _on_llm_request(**kwargs: Any) -> Optional[Dict[str, Any]]:
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None
    updated = masker.mask_llm_request(request, _session_id(**kwargs))
    if updated is request:
        return None
    return {
        "request": updated,
        "source": "pii-mask",
        "reason": "masked private values for the provider",
    }


def _on_tool_request(**kwargs: Any) -> Optional[Dict[str, Any]]:
    args = kwargs.get("args")
    if not isinstance(args, dict):
        return None
    table = masker.get_map(_session_id(**kwargs))
    updated = masker.unmask_value(args, table)
    if updated == args:
        return None
    return {
        "args": updated,
        "source": "pii-mask",
        "reason": "restored private values for tool execution",
    }


def _on_transform_llm_output(response_text: str = "", **kwargs: Any) -> Optional[str]:
    if not response_text or "[" not in response_text:
        return None
    table = masker.get_map(_session_id(**kwargs))
    restored = table.unmask_text(response_text)
    return restored if restored != response_text else None


def _looks_like_file(raw: str) -> Optional[Path]:
    if "\n" in raw or "\r" in raw:
        return None
    candidate = Path(raw.strip().strip("\"'")).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved.is_file():
        return resolved
    return None


def _read_file_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        binary = shutil.which("pdftotext")
        if not binary:
            raise RuntimeError("outil de lecture PDF absent")
        result = subprocess.run(
            [binary, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(err or "lecture PDF impossible")
        return (result.stdout or b"").decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8")


def _write_masked_file(source: Path, masked: str) -> Path:
    suffix = ".md" if source.suffix.lower() == ".pdf" else (source.suffix or ".txt")
    dest = source.with_name(f"{source.stem}.masque{suffix}")
    n = 2
    while dest.exists():
        dest = source.with_name(f"{source.stem}.masque-{n}{suffix}")
        n += 1
    dest.write_text(masked, encoding="utf-8")
    return dest


def _spill_long_text(masked: str) -> Path:
    folder = get_hermes_home() / "cache" / "scratch" / "masquer"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "texte.masque.txt"
    n = 2
    while dest.exists():
        dest = folder / f"texte.masque-{n}.txt"
        n += 1
    dest.write_text(masked, encoding="utf-8")
    return dest


def _anonymize(raw: str) -> str:
    table = masker.get_map()
    path = _looks_like_file(raw)
    if path is not None:
        size = path.stat().st_size
        if size > _MAX_FILE:
            return "Fichier trop lourd (max 5 Mo)."
        try:
            original = _read_file_text(path)
        except UnicodeDecodeError:
            return "Je ne masque que les fichiers texte."
        except Exception as exc:
            return f"Impossible de lire ce fichier : {exc}"
        masked = table.mask_text(original)
        if masked == original:
            return "Rien à masquer dans ce fichier."
        dest = _write_masked_file(path, masked)
        return f"Version masquée : {dest}"

    masked = table.mask_text(raw)
    if masked == raw:
        return "Rien à masquer dans ce texte."
    if len(masked) > _CHAT_LIMIT:
        dest = _spill_long_text(masked)
        return f"Texte trop long pour Discord. Version masquée : {dest}"
    return masked


def _on_slash(raw_args: str) -> str:
    arg = (raw_args or "").strip()
    key = arg.lower()
    if key in {"", "status", "etat", "état"}:
        return masker.status_text()
    if key in {"off"}:
        masker.set_global_enabled(False)
        return "Mode auto éteint. Les messages partent tels quels au modèle. /masquer … marche encore à la demande."
    if key in {"on"}:
        masker.set_global_enabled(True)
        return "Mode auto allumé. Emails, téléphones, IBAN, adresses FR, clés, et les noms de ta liste."
    if key in {"help", "aide"}:
        return _HELP
    if key in {"liste", "list"}:
        return masker.terms().list_text()
    first, _, rest = arg.partition(" ")
    head = first.lower()
    if head in {"ajouter", "add"} and rest.strip():
        return masker.terms().add(rest)
    if head in {"retirer", "enlever", "remove"} and rest.strip():
        return masker.terms().remove(rest)
    return _anonymize(arg)


def register(ctx) -> None:
    masker.set_terms_path(get_hermes_home() / "pii-mask" / "termes.txt")
    ctx.register_middleware("llm_request", _on_llm_request)
    ctx.register_middleware("tool_request", _on_tool_request)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
    ctx.register_command(
        "masquer",
        handler=_on_slash,
        description="Masquer des données perso : status, on, off, ajouter, liste, texte ou fichier.",
        args_hint="[status|on|off|ajouter|liste|texte ou fichier]",
        argument_mode="text",
    )
    ctx.register_system_prompt_section(
        "pii-mask.notice",
        _NOTICE,
        position="after_memory",
        max_chars=400,
    )
