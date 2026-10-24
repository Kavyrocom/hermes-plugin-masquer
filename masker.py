"""Round-trip mask: high-confidence patterns plus an explicit name/address list."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VERSION = "0.3.0"

# Order: more specific first. No Discord snowflakes. No bare first names.
_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(
            r"(?<![a-zA-Z0-9._%+\-])(?!git@)[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\."
            r"(?!(?:service|socket|timer|target|mount|path|slice|scope"
            r"|png|jpg|jpeg|gif|svg|webp|ico|avif|pdf|zip|gz|tgz|bz2"
            r"|js|ts|py|php|json|csv|test|invalid|example|localhost)\b)[a-zA-Z]{2,}\b"
        ),
    ),
    (
        "IBAN",
        re.compile(r"\bFR\d{2}(?:\s?\d{4}){5}\s?\d{3}\b", re.IGNORECASE),
    ),
    (
        # Generic IBAN: any country, kept only when the ISO 7064 mod-97 check passes.
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,6}(?: ?[A-Z0-9]{1,4})?\b"),
    ),
    (
        # French mobile/landline: 0X, +33/0033 X, +33 (0)X, (+33) X.
        "PHONE",
        re.compile(
            r"(?<![\w.])(?:\(?(?:\+|00)33\)?[\s.\-]*(?:\(0\)[\s.\-]*)?|0)"
            r"[1-9](?:[\s.\-]*\d{2}){4}(?!\d)"
        ),
    ),
    (
        # International: 10 to 15 digits after + or 00. A date like "+33 2026-10-24"
        # only has 8 digits and never matches.
        "PHONE",
        re.compile(r"(?<![\w.])(?:\+|00)\d(?:[\s.\-]?\d){10,14}(?!\d)"),
    ),
    (
        "API_KEY",
        re.compile(
            r"\b(?:sk-ant-[a-zA-Z0-9_\-]{20,}|sk-[a-zA-Z0-9_\-]{20,}"
            r"|gh[pousr]_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{20,}"
            r"|[sr]k_(?:live|test)_[a-zA-Z0-9]{16,}"
            r"|whsec_[a-zA-Z0-9]{20,}"
            r"|(?:secret|ntn)_[a-zA-Z0-9]{20,}"
            r"|pat[A-Za-z0-9]{14}\.[A-Za-z0-9]{16,}"
            r"|\d{8,10}:AA[A-Za-z0-9_\-]{30,}"
            r"|[MN][A-Za-z0-9_\-]{20,26}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{25,45}"
            r"|ya29\.[A-Za-z0-9_\-]{20,}"
            r"|AIza[a-zA-Z0-9_\-]{35}"
            r"|eyJ[a-zA-Z0-9_\-]{8,}\.eyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]{8,}"
            r"|xox[abprs]-[a-zA-Z0-9\-]{10,}"
            r"|AKIA[A-Z0-9]{16})(?![a-zA-Z0-9_\-])"
        ),
    ),
    (
        "API_KEY",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{10,}?-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    (
        # French street address: "12 rue de la Paix", "3 bis avenue Foch".
        "ADDRESS",
        re.compile(
            r"\b\d{1,4}(?:\s*(?:bis|ter|quater))?\s*,?\s+"
            r"(?:rue|avenue|av\.|boulevard|bd|chemin|impasse|place|allée|allee|"
            r"cours|quai|route|rte|passage|square|villa)\s+"
            r"[A-Za-zÀ-ÿ'\u2019\-]+(?:\s+[A-Za-zÀ-ÿ'\u2019\-]+){0,2}",
            re.IGNORECASE,
        ),
    ),
    (
        # Credit card: grouped 4-digit blocks or 15-16 contiguous digits, Luhn-validated.
        "CARD",
        re.compile(r"\b(?:\d{4}[ \-]?){3}\d{1,4}(?!\d)|(?<!\d)\d{15,16}(?!\d)"),
    ),
]

_PLACEHOLDER_RE = re.compile(r"\[(EMAIL|IBAN|PHONE|API_KEY|ADDRESS|NOM|CARD)_(\d+)\]")
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
    "CARD": ("carte", "cartes"),
    "NOM": ("nom", "noms"),
}

# Words that alone are not a street name; the validator wants a real name after them.
_STREET_WORDS = re.compile(
    r"^(?:rue|avenue|av\.|boulevard|bd|chemin|impasse|place|allée|allee|"
    r"cours|quai|route|rte|passage|square|villa|bis|ter|quater|de|du|des|la|le|les|"
    r"aux|d|l|dela)$",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total, double = 0, False
    for ch in reversed(digits):
        n = ord(ch) - 48
        if double:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        double = not double
    return total % 10 == 0


def _iban_mod97_ok(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if len(compact) < 5 or len(compact) > 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    total = 0
    for ch in rearranged:
        if ch.isalpha():
            total = total * 100 + (ord(ch) - 55)
        elif ch.isdigit():
            total = total * 10 + (ord(ch) - 48)
        else:
            return False
    return total % 97 == 1


def _address_has_name(value: str) -> bool:
    """A street hit must carry a capitalized name: « rue de la Paix » yes,
    « 5 route show default » (tool output) no."""
    for word in value.split():
        if _STREET_WORDS.match(word):
            continue
        if re.search(r"(?:^|['\u2019])[A-ZÀ-Ÿ]", word):
            return True
    return False


_VALIDATORS: Dict[str, Callable[[str], bool]] = {
    "IBAN": _iban_mod97_ok,
    "CARD": lambda v: _luhn_ok(re.sub(r"\D", "", v)),
    "ADDRESS": _address_has_name,
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
                check = _VALIDATORS.get(kind)
                if check is not None and not check(value):
                    continue
                matches.append((m.start(), m.end(), kind, m.group(0)))
        # A placeholder this table already emits stays untouched: the model reusing a
        # token it was given (tool args, thinking) must read back the same token.
        # A literal placeholder pasted from a document still gets its own mapping.
        for m in _PLACEHOLDER_RE.finditer(text):
            if m.group(0) in self.placeholder_to_original:
                continue
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
            def _repl(match: "re.Match[str]") -> str:
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


def drop_map(session_id: str) -> None:
    """Forget a session's table: called on session end/reset/delete so the
    in-memory store of private values does not outlive the conversation."""
    key = session_id or "_default"
    with _maps_lock:
        _maps.pop(key, None)


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


def self_test() -> Tuple[int, int, List[str]]:
    """Mask-and-restore check on fake values; returns (ok, total, failures)."""
    samples = [
        ("email", "jean.dupont" + chr(64) + "exemple.fr"),
        ("téléphone FR", "[PHONE_21]"),
        ("téléphone +33 (0)", "+33 (0)6 12 34 56 78"),
        ("téléphone intl", "+1 415 555 0132"),
        ("IBAN FR", "[IBAN_4]"),
        ("IBAN BE", "BE68 5390 0754 7034"),
        ("carte bancaire", "4242 4242 4242 4242"),
        ("clé API", "sk-ant-" + "a1B2" * 6),
        ("adresse", "[ADDRESS_46]"),
    ]
    failures: List[str] = []
    for label, sample in samples:
        table = PiiMap()
        masked = table.mask_text(sample)
        if masked == sample:
            failures.append(label + " non masqué")
        elif table.unmask_text(masked) != sample:
            failures.append(label + " ne revient pas en clair")
    return len(samples) - len(failures), len(samples), failures


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
    ok, total, failures = self_test()
    test_bit = (
        f" Auto-test : {ok}/{total} OK."
        if ok == total
        else f" Auto-test : {ok}/{total}, échec : {', '.join(failures[:3])}."
    )
    if not counts:
        return f"Mode auto {auto} (plugin {VERSION}).{extra_bit}{test_bit} Rien de masqué pour l'instant."
    parts = []
    for kind, n in sorted(counts.items()):
        one, many = _LABELS.get(kind, (kind.lower(), kind.lower() + "s"))
        parts.append(f"{n} {many if n > 1 else one}")
    return (
        f"Mode auto {auto} (plugin {VERSION}).{extra_bit}{test_bit} Masqué : {', '.join(parts)}. "
        "Toi tu continues à tout lire normalement."
    )


def mask_value(value: Any, table: PiiMap) -> Any:
    if isinstance(value, str):
        return table.mask_text(value)
    if isinstance(value, list):
        return [mask_value(item, table) for item in value]
    if isinstance(value, dict):
        # A signed thinking block is tied to its exact text: masking it would break
        # the signature and the provider would reject the replayed turn with a 400.
        if "signature" in value and ("thinking" in value or "reasoning" in value):
            return value
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