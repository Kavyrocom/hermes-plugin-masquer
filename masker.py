"""Round-trip mask: high-confidence patterns plus an explicit name/address list."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Order: more specific first. No Discord snowflakes. No bare first names.
_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(
            r"(?<![a-zA-Z0-9._%+\-])[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\."
            r"(?!(?:service|socket|timer|target|mount|path|slice|scope)\b)[a-zA-Z]{2,}\b"
        ),
    ),
    (
        "IBAN",
        re.compile(r"\bFR\d{2}(?:\s?\d{4}){5}\s?\d{3}\b", re.IGNORECASE),
    ),
    (
        "PHONE",
        re.compile(
            r"(?<!\d)(?:(?:\+|00)33[\s.\-]*|0)[1-9](?:[\s.\-]*\d{2}){4}(?!\d)"
        ),
    ),
    (
        "API_KEY",
        re.compile(
            r"\b(?:sk-ant-[a-zA-Z0-9_\-]{20,}|sk-[a-zA-Z0-9_\-]{20,}"
            r"|gh[pousr]_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{20,}"
            r"|[sr]k_(?:live|test)_[a-zA-Z0-9]{16,}"
            r"|xox[abprs]-[a-zA-Z0-9\-]{10,}"
            r"|AIza[a-zA-Z0-9_\-]{35}"
            r"|eyJ[a-zA-Z0-9_\-]{8,}\.eyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]{8,}"
            r"|AKIA[A-Z0-9]{16})(?![a-zA-Z0-9_\-])"
        ),
    ),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,4}(?:\s*(?:bis|ter|quater))?\s+"
            r"(?:rue|avenue|av\.|boulevard|bd|chemin|impasse|place|allée|allee|"
            r"cours|quai|route|rte|passage|square|villa)\s+"
            r"[A-Za-zÀ-ÿ'\-]+(?:\s+[A-Za-zÀ-ÿ'\-]+){0,2}",
            re.IGNORECASE,
        ),
    ),
    (
        "ADDRESS",
        re.compile(
            r"\b(?:0[1-9]|[1-8]\d|9[0-5]|97[1-6]|98[0-9])\d{3}\s+"
            r"[A-ZÀ-Ÿ][A-Za-zÀ-ÿ'\-]+(?:[\s\-][A-ZÀ-Ÿ][A-Za-zÀ-ÿ'\-]+)?\b"
        ),
    ),
]

_PLACEHOLDER_RE = re.compile(r"\[(EMAIL|IBAN|PHONE|API_KEY|ADDRESS|NOM)_(\d+)\]")
_CHUNK = 500_000
_WALK_KEYS = (
    "messages", "input", "instructions", "system", "tools", "prompt",
    "contents", "system_instruction", "systemInstruction",
)
_NEVER = frozenset({
    "david", "kavyro", "dante", "hermes", "octavia", "simaris", "ember",
    "aura", "lavos", "khora", "discord", "telegram", "whatsapp", "paris",
    "france", "ia", "agent",
})
_LABELS = {
    "EMAIL": ("email", "emails"),
    "PHONE": ("téléphone", "téléphones"),
    "IBAN": ("IBAN", "IBAN"),
    "API_KEY": ("clé", "clés"),
    "ADDRESS": ("adresse", "adresses"),
    "NOM": ("nom", "noms"),
}


class TermStore:
    """Names and extra phrases David adds. Never guessed."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.terms: List[str] = []
        if path:
            self.load()

    def load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.terms = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(self.terms) + ("\n" if self.terms else "")
        self.path.write_text(body, encoding="utf-8")

    def add(self, term: str) -> str:
        clean = " ".join(term.split())
        if len(clean) < 3:
            return "Trop court. Donne un nom complet, une société, ou une adresse."
        if clean.lower() in _NEVER:
            return f"« {clean} » est trop courant ici. On ne le masque pas, sinon l'agent ne comprend plus."
        with self._lock:
            for existing in self.terms:
                if existing.lower() == clean.lower():
                    return f"Déjà dans la liste : {existing}"
            self.terms.append(clean)
            self.save()
        return f"Ajouté : {clean}. Le mode auto le masquera."

    def remove(self, term: str) -> str:
        needle = " ".join(term.split()).lower()
        with self._lock:
            kept = [t for t in self.terms if t.lower() != needle]
            if len(kept) == len(self.terms):
                return "Pas trouvé dans la liste."
            self.terms = kept
            self.save()
        return "Retiré de la liste."

    def list_text(self) -> str:
        if not self.terms:
            return "Liste vide. Ajoute un nom avec /masquer ajouter Marie Dupont"
        lines = "\n".join(f"- {t}" for t in self.terms)
        return f"Noms et termes à masquer ({len(self.terms)}) :\n{lines}"


_terms = TermStore()


def set_terms_path(path: Path) -> None:
    global _terms
    _terms = TermStore(path)


def terms() -> TermStore:
    return _terms


def _chunks(text: str) -> List[str]:
    """Cut a long text at newlines (else spaces) so no value is skipped."""
    out: List[str] = []
    start, size = 0, len(text)
    while start < size:
        end = min(start + _CHUNK, size)
        if end < size:
            floor = start + _CHUNK // 2
            cut = text.rfind("\n", floor, end)
            if cut < 0:
                cut = text.rfind(" ", floor, end)
            if cut >= 0:
                end = cut + 1
        out.append(text[start:end])
        start = end
    return out


def _term_regex(term: str) -> "re.Pattern[str]":
    """Whole-word match: 'Ana' must not hit 'banane'."""
    left = r"(?<!\w)" if re.match(r"\w", term[0]) else ""
    right = r"(?!\w)" if re.match(r"\w", term[-1]) else ""
    return re.compile(left + re.escape(term) + right, re.IGNORECASE)


class PiiMap:
    """One mapping table (typically one session)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = True
        self.original_to_placeholder: Dict[str, str] = {}
        self.placeholder_to_original: Dict[str, str] = {}
        self._next_id: Dict[str, int] = {}

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for placeholder in self.placeholder_to_original:
            kind = placeholder.strip("[]").rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return out

    def mask_text(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            if len(text) <= _CHUNK:
                return self._mask_chunk(text)
            return "".join(self._mask_chunk(part) for part in _chunks(text))

    def _mask_chunk(self, text: str) -> str:
        matches: List[Tuple[int, int, str, str]] = []
        for kind, rx in _PATTERNS:
            for m in rx.finditer(text):
                value = m.group(0).strip()
                if len(value) < 5 and kind == "ADDRESS":
                    continue
                matches.append((m.start(), m.end(), kind, m.group(0)))
        # A real text that already contains a token such as the first email placeholder
        # must not be restored as a private value later: give it its own placeholder.
        for m in _PLACEHOLDER_RE.finditer(text):
            matches.append((m.start(), m.end(), m.group(1), m.group(0)))
        extra = sorted(_terms.terms, key=len, reverse=True)
        for term in extra:
            if term.lower() in _NEVER:
                continue
            for m in _term_regex(term).finditer(text):
                matches.append((m.start(), m.end(), "NOM", m.group(0)))
        if not matches:
            return text
        matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        kept: List[Tuple[int, int, str, str]] = []
        last_end = -1
        for start, end, kind, value in matches:
            if start < last_end:
                continue
            kept.append((start, end, kind, value))
            last_end = end
        pieces: List[str] = []
        cursor = 0
        for start, end, kind, value in kept:
            pieces.append(text[cursor:start])
            pieces.append(self._placeholder_for(kind, value))
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    def unmask_text(self, text: str) -> str:
        if not text or "[" not in text:
            return text
        with self._lock:
            def _repl(match: re.Match[str]) -> str:
                token = match.group(0)
                return self.placeholder_to_original.get(token, token)

            return _PLACEHOLDER_RE.sub(_repl, text)

    def _placeholder_for(self, kind: str, value: str) -> str:
        existing = self.original_to_placeholder.get(value)
        if existing:
            return existing
        n = self._next_id.get(kind, 1)
        self._next_id[kind] = n + 1
        placeholder = f"[{kind}_{n}]"
        self.original_to_placeholder[value] = placeholder
        self.placeholder_to_original[placeholder] = value
        return placeholder


_maps: Dict[str, PiiMap] = {}
_maps_lock = threading.Lock()
_global_enabled = True


def get_map(session_id: str = "") -> PiiMap:
    key = session_id or "_default"
    with _maps_lock:
        found = _maps.get(key)
        if found is None:
            found = PiiMap()
            found.enabled = _global_enabled
            _maps[key] = found
        return found


def set_global_enabled(enabled: bool) -> None:
    global _global_enabled
    _global_enabled = enabled
    with _maps_lock:
        for item in _maps.values():
            item.enabled = enabled


def is_global_enabled() -> bool:
    return _global_enabled


def _all_counts() -> Dict[str, int]:
    totals: Dict[str, int] = {}
    with _maps_lock:
        tables = list(_maps.values())
    for table in tables:
        for kind, n in table.counts().items():
            totals[kind] = totals.get(kind, 0) + n
    return totals


def status_text(session_id: str = "") -> str:
    """The slash command has no session id: with none, count every session."""
    if session_id:
        table = get_map(session_id)
        auto = "allumé" if table.enabled else "éteint"
        counts = table.counts()
    else:
        auto = "allumé" if _global_enabled else "éteint"
        counts = _all_counts()
    extra = len(_terms.terms)
    extra_bit = f" Liste perso : {extra} nom{'s' if extra != 1 else ''}." if extra else " Liste perso vide."
    if not counts:
        return f"Mode auto {auto}.{extra_bit} Rien de masqué pour l'instant."
    parts = []
    for kind, n in sorted(counts.items()):
        one, many = _LABELS.get(kind, (kind.lower(), kind.lower() + "s"))
        parts.append(f"{n} {many if n > 1 else one}")
    return (
        f"Mode auto {auto}.{extra_bit} Masqué : {', '.join(parts)}. "
        "Toi tu continues à tout lire normalement."
    )


def mask_value(value: Any, table: PiiMap) -> Any:
    if isinstance(value, str):
        return table.mask_text(value)
    if isinstance(value, list):
        return [mask_value(item, table) for item in value]
    if isinstance(value, dict):
        return {
            key: _mask_arguments(item, table) if key == "arguments" and isinstance(item, str)
            else mask_value(item, table)
            for key, item in value.items()
        }
    return value


def _mask_arguments(raw: str, table: PiiMap) -> str:
    """Tool-call arguments are a JSON string: mask the decoded values, never the
    raw text, or a regex hit on an escape such as the n of \\n can leave a lone
    backslash and make the provider reject the call as invalid JSON."""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return table.mask_text(raw)
    return json.dumps(mask_value(decoded, table), ensure_ascii=False)


def unmask_value(value: Any, table: PiiMap) -> Any:
    if isinstance(value, str):
        return table.unmask_text(value)
    if isinstance(value, list):
        return [unmask_value(item, table) for item in value]
    if isinstance(value, dict):
        return {key: unmask_value(item, table) for key, item in value.items()}
    return value


def mask_llm_request(request: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    table = get_map(session_id)
    if not table.enabled:
        return request
    updated = dict(request)
    for key in _WALK_KEYS:
        if key in updated:
            updated[key] = mask_value(updated[key], table)
    return request if updated == request else updated
