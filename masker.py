"""Round-trip mask: high-confidence patterns, person names, and an explicit list."""

from __future__ import annotations

import fnmatch
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from . import noms
except ImportError:  # running the file outside the package (tests)
    import noms  # type: ignore

VERSION = "0.4.0"

# Order: more specific first. No Discord snowflakes. No bare first names.
_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(
            # Start after a JSON escape (\n, \r, \t) or at a clean boundary, never on
            # the escape letter itself: "\n@decorator.name" in a serialized tool
            # output is not an email.
            r"(?:(?<=\\[nrt])|(?<![a-zA-Z0-9._%+\-\\]))(?!git@)[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\."
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
        # Credit card: 15 or 16 digits, grouped by 4 or contiguous, Luhn-validated.
        # Shorter runs are left alone: a 13-digit millisecond timestamp passes
        # Luhn one time in ten.
        "CARD",
        re.compile(r"\b(?:\d{4}[ \-]?){3}\d{3,4}(?!\d)|(?<!\d)\d{15,16}(?!\d)"),
    ),
]

_PLACEHOLDER_RE = re.compile(r"\[(EMAIL|IBAN|PHONE|API_KEY|ADDRESS|PRENOM|NOM|CARD)_(\d+)\]")
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
    "PRENOM": ("prénom", "prénoms"),
}

# Where a text comes from decides how names are treated:
#   private: names are detected, masked and remembered for the session;
#   known:   only names already seen in private text are masked.
PRIVATE, KNOWN = "private", "known"
# Results of these tools are public web text: a minister or « Louis Vuitton »
# stays readable. Anything else (files, mail, CRM, browser pages) is private.
_WEB_TOOLS = ("*web_search*", "*web_extract*", "*web_fetch*", "*websearch*", "*webfetch*", "x_search")
_KNOWN_KEYS = frozenset({"system", "instructions", "tools", "system_instruction", "systemInstruction"})
_KNOWN_ROLES = frozenset({"assistant", "model", "system", "developer"})

# Words that alone are not a street name; the validator wants a real name after them.
_STREET_WORDS = re.compile(
    r"^(?:rue|avenue|av\.|boulevard|bd|chemin|impasse|place|allée|allee|"
    r"cours|quai|route|rte|passage|square|villa|bis|ter|quater|de|du|des|la|le|les|"
    r"aux|d|l|dela)$",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    if not 15 <= len(digits) <= 16:
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
_names_enabled = True
_excluded: Set[str] = set()
_web_tools: Tuple[str, ...] = _WEB_TOOLS
_config_dir: Optional[Path] = None
_names: Optional["noms.Names"] = None


def _read_lines(path: Path) -> List[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def set_terms_path(path: Path) -> None:
    """`path` is the personal list; the same folder holds the name settings:
    exclus.txt (words never treated as a name), outils-web.txt (tools whose
    results are public web text) and noms-off (name detection switched off)."""
    global _terms, _config_dir, _excluded, _web_tools, _names, _names_enabled
    _terms = TermStore(path)
    _config_dir = path.parent
    _excluded = {noms.norm(w) for w in _read_lines(_config_dir / "exclus.txt")}
    _web_tools = tuple(_read_lines(_config_dir / "outils-web.txt")) or _WEB_TOOLS
    _names_enabled = not (_config_dir / "noms-off").exists()
    _names = None


def _detector() -> Optional["noms.Names"]:
    global _names
    if not _names_enabled or not noms.available():
        return None
    if _names is None:
        _names = noms.Names(set(_NEVER) | _excluded)
    return _names


def names_enabled() -> bool:
    return _names_enabled and noms.available()


def set_names_enabled(enabled: bool) -> None:
    global _names_enabled
    _names_enabled = enabled
    if _config_dir:
        flag = _config_dir / "noms-off"
        if enabled:
            flag.unlink(missing_ok=True)
        else:
            _config_dir.mkdir(parents=True, exist_ok=True)
            flag.write_text("", encoding="utf-8")


def exclude_name(word: str) -> str:
    """A word the user never wants treated as a person: a brand, a product, an agent."""
    global _names
    clean = " ".join(word.split())
    if len(clean) < 2:
        return "Donne le mot à ne jamais masquer."
    for part in clean.split():
        _excluded.add(noms.norm(part))
    _names = None
    with _maps_lock:
        for table in _maps.values():
            for part in clean.split():
                table.known.pop(noms.norm(part), None)
    if _config_dir:
        _config_dir.mkdir(parents=True, exist_ok=True)
        (_config_dir / "exclus.txt").write_text("\n".join(sorted(_excluded)) + "\n", encoding="utf-8")
    return f"« {clean} » ne sera plus pris pour un nom de personne."


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
        # Names seen in private text during this session: word -> PRENOM | NOM.
        self.known: Dict[str, str] = {}
        self._scanned: Set[int] = set()

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for placeholder in self.placeholder_to_original:
            kind = placeholder.strip("[]").rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return out

    def learn(self, text: str, mode: str = PRIVATE) -> str:
        """Remember the names of a private text without masking it. Run on the
        whole request first, so a name met late is masked from the first line."""
        detector = _detector()
        if detector is None or mode != PRIVATE or not text:
            return text
        key = hash(text)
        with self._lock:
            if key not in self._scanned:
                self._scanned.add(key)
                detector.detect(text, self.known)
        return text

    def mask_text(self, text: str, mode: str = PRIVATE) -> str:
        if not text:
            return text
        with self._lock:
            if len(text) <= _CHUNK:
                return self._mask_chunk(text, mode)
            return "".join(self._mask_chunk(part, mode) for part in _chunks(text))

    def _mask_chunk(self, text: str, mode: str = PRIVATE) -> str:
        matches: List[Tuple[int, int, str, str]] = []
        detector = _detector()
        if detector is not None:
            if mode == PRIVATE:
                matches.extend(detector.detect(text, self.known))
            else:
                matches.extend(detector.known_spans(text, self.known))
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
        # An email is the same address whatever its case: one token, restored
        # with the first spelling seen.
        if kind == "EMAIL":
            key = value.lower()
        elif kind in ("NOM", "PRENOM"):
            # « DUPONT », « Dupont » and « dupont » are one person: one token.
            key = kind + ":" + noms.norm(value)
        else:
            key = value
        existing = self.original_to_placeholder.get(key)
        if existing:
            return existing
        n = self._next_id.get(kind, 1)
        self._next_id[kind] = n + 1
        placeholder = f"[{kind}_{n}]"
        self.original_to_placeholder[key] = placeholder
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
    if names_enabled():
        samples.append(("nom de personne", "Bonjour, je suis Camille Lefebvre."))
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


def _is_web_tool(name: str) -> bool:
    low = (name or "").lower()
    return any(fnmatch.fnmatch(low, pattern.lower()) for pattern in _web_tools)


def _tool_names(value: Any, out: Dict[str, str]) -> None:
    """Map each tool-call id to its tool name, whatever the provider format."""
    if isinstance(value, list):
        for item in value:
            _tool_names(item, out)
    elif isinstance(value, dict):
        ident = value.get("call_id") or value.get("id")
        fn = value.get("function")
        name = fn.get("name") if isinstance(fn, dict) else value.get("name")
        if isinstance(ident, str) and isinstance(name, str) and (
            isinstance(fn, dict) or value.get("type") in ("tool_use", "function_call", "server_tool_use")
        ):
            out[ident] = name
        for item in value.values():
            if isinstance(item, (list, dict)):
                _tool_names(item, out)


def _mode_of(value: Dict[str, Any], mode: str, tools: Dict[str, str]) -> str:
    """Provenance of one message or content block."""
    role, kind = value.get("role"), value.get("type")
    if role in _KNOWN_ROLES:
        return KNOWN
    if role == "user":
        return PRIVATE
    name: Optional[str] = None
    if role == "tool" or kind in ("tool_result", "function_call_output"):
        ident = value.get("tool_call_id") or value.get("tool_use_id") or value.get("call_id")
        name = tools.get(ident, "") if isinstance(ident, str) else ""
        if not name and isinstance(value.get("name"), str):
            name = value["name"]
    elif isinstance(value.get("functionResponse"), dict):
        name = str(value["functionResponse"].get("name") or "")
    if name is not None:
        return KNOWN if _is_web_tool(name) else PRIVATE
    return mode


def mask_value(
    value: Any,
    table: PiiMap,
    mode: str = PRIVATE,
    tools: Optional[Dict[str, str]] = None,
    learn: bool = False,
) -> Any:
    if isinstance(value, str):
        return table.learn(value, mode) if learn else table.mask_text(value, mode)
    if isinstance(value, list):
        return [mask_value(item, table, mode, tools, learn) for item in value]
    if isinstance(value, dict):
        # A signed thinking block is tied to its exact text: masking it would break
        # the signature and the provider would reject the replayed turn with a 400.
        if "signature" in value and ("thinking" in value or "reasoning" in value):
            return value
        here = _mode_of(value, mode, tools or {})
        return {
            key: _mask_arguments(item, table, here, learn) if key == "arguments" and isinstance(item, str)
            else mask_value(item, table, here, tools, learn)
            for key, item in value.items()
        }
    return value


def _mask_arguments(raw: str, table: PiiMap, mode: str = PRIVATE, learn: bool = False) -> str:
    """Tool-call arguments are a JSON string: mask the decoded values, never the
    raw text, or a regex hit on an escape such as the n of \\n can leave a lone
    backslash and make the provider reject the call as invalid JSON."""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return table.learn(raw, mode) if learn else table.mask_text(raw, mode)
    if learn:
        mask_value(decoded, table, mode, None, True)
        return raw
    masked = mask_value(decoded, table, mode)
    return raw if masked == decoded else json.dumps(masked, ensure_ascii=False)


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
    tools: Dict[str, str] = {}
    for key in _WALK_KEYS:
        if key in updated and key not in _KNOWN_KEYS:
            _tool_names(updated[key], tools)
    modes = {key: KNOWN if key in _KNOWN_KEYS else PRIVATE for key in _WALK_KEYS if key in updated}
    if _detector() is not None:
        for key, mode in modes.items():
            mask_value(updated[key], table, mode, tools, learn=True)
    for key, mode in modes.items():
        updated[key] = mask_value(updated[key], table, mode, tools)
    return request if updated == request else updated