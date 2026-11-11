"""Person-name detection without any model: embedded dictionaries, structured
fields, context cues, and a per-session memory of names already seen.

A name is masked on first sight only in private text. Text coming from the web
keeps its names, unless the same name was already seen in private text."""

from __future__ import annotations

import gzip
import re
import threading
import unicodedata
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

_DATA = Path(__file__).resolve().parent / "data"
Span = Tuple[int, int, str, str]

_UP = r"A-ZÀ-ÖØ-Þ\u0100-\u017f"
_LO = r"a-zß-öø-ÿ\u0100-\u017f"
_T = rf"(?:Mc|Mac|O['\u2019]|D['\u2019])?[{_UP}][{_LO}]+(?:-[{_UP}][{_LO}]+)?"
_U = rf"[{_UP}]{{2,}}(?:-[{_UP}]{{2,}})?"
_W = rf"(?:{_T}|{_U})"
_P = r"(?:de|du|des|le|la|van|von|der|den|di|da|del|dos|ben|bin|el|al|De|Du|Le|La|Van|Von|Di|Da|Del|Ben|El|Al)"
_SP = r"[ \u00a0]"
# Start after a JSON escape (\n, \r, \t) or at a clean boundary; never inside a
# path, an email, a URL or an identifier.
_B = rf"(?:(?<=\\[nrt])|(?<![\w@./\\\-'\u2019]))"
_E = r"(?![\w@]|\.[a-z]|-\w|/\w)"

_FULL = re.compile(rf"{_B}({_W}){_SP}((?:{_P}{_SP})?{_W})(?:{_SP}({_U}))?{_E}")
_TOKEN = re.compile(rf"{_B}({_W}){_E}")
_GREET = re.compile(
    rf"{_B}(?:Bonjour|Bonsoir|Salut|Hello|Hi|Hey|Coucou|Cher|Chère|Dear|Merci){_SP}+({_T}){_E}"
)
_CIV = re.compile(
    rf"{_B}(?:M\.|Mme|Mlle|Monsieur|Madame|Dr\.?|Me|Mr\.?|Mrs\.?|Ms\.?|Pr\.?){_SP}((?:{_P}{_SP})?{_W}){_E}"
)
_NL = r"(?:\\n|\\r|[\r\n]|[ \t])"
_SIGN = re.compile(
    r"(?:Cordialement|Bien à vous|Amicalement|À bientôt|A bientôt|Sincèrement|Bonne journée|"
    r"Regards|Best|Cheers|Thanks|Sincerely)[,.! ]*"
    rf"{_NL}*(?:\\n|[\r\n]){_NL}*({_T})(?:{_SP}((?:{_P}{_SP})?{_W}))?{_E}"
)
_Q = r"""\\*["']"""
_K_FIRST = r"first_?name|firstName|prenom|prénom|Prénom|Prenom|given_?name|givenName"
_K_LAST = r"last_?name|lastName|family_?name|familyName|nom_de_famille|nom_famille|surname"
_K_FULL = (
    r"full_?name|fullName|display_?name|displayName|customer_?name|customerName|billing_?name"
    r"|shipping_?name|author_?name|contact_?name|contactName|recipient_?name|real_?name"
    r"|attendee_?name|cardholder_?name|nom_complet|nom_client"
)
_K_LOOSE = r"name|Name|nom|Nom|author|Author|auteur|Auteur|owner|contact|Contact|client|Client|From|De|To|Cc"
_KV = re.compile(
    rf"(?<![\w.\-])(?:{_Q})?({_K_FIRST}|{_K_LAST}|{_K_FULL}|{_K_LOOSE})(?:{_Q})?[ \t]*[:=][ \t]*(?:{_Q})?"
    r"""([^"'\\\n\r\t,;:={}\[\]<>|()/@]{2,60})"""
)
_HDR = re.compile(rf"{_B}({_W}(?:{_SP}(?:{_P}{_SP})?{_W}){{1,2}}) ?<(?=[^<>\s]+@)")
_NAMEISH = re.compile(rf"^[A-Za-z{_UP}{_LO}][A-Za-z{_UP}{_LO}'\u2019\- .]{{1,58}}$")
_SPLIT = re.compile(r"[ \u00a0]+")
_PARTICLE = re.compile(rf"^{_P}$")

# Never a person here, whatever the context.
_STATIC = frozenset(
    """january february march april may june july august september october november december
janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre
monday tuesday wednesday thursday friday saturday sunday lundi mardi mercredi jeudi vendredi
samedi dimanche saint sainte san santa mont port fort new north south east west nord sud est
ouest claude hermes gemini siri alexa cortana watson jarvis llama mistral ada julia python
ruby rust swift java perl dart crystal elixir jenkins docker travis jira jest mocha jasmine
cypress lambda delta sigma alpha beta gamma omega null none true false string type object
user admin root test example demo unknown anonymous anonyme""".split()
)

# After a first name, these words signal a company or a product, not a person.
_ORG = frozenset(
    """moto motos motor motors sport sports digital lab labs group groupe organisation organization
distribution center centre factory studio studios media consulting conseil conseils services
service solutions tech web design premium pro mini plus max automobile automobiles auto autos
cars shop store agency agence company corp inc ltd sarl sas sasu eurl gmbh team club academy
academie school ecole university universite institute institut foundation fondation hotel
restaurant cafe bar garage immobilier immo finance bank banque capital partners holding
holdings international global systems software apps app cloud data blocks shares cost pilot
code agent agents api cli sdk bot pay mail drive docs maps news tv radio magazine editions
production productions records music films film pictures games gaming energy energie
industries industrie logistics transport transports travel voyages voyage tours tour ride
rides racing bikes bike cycles electric paris france europe usa""".split()
)

# « Groupe Martin », « Circuit Paul Ricard » : the name of a place or a company.
_ORG_BEFORE = re.compile(
    r"(?:Groupe|Group|Circuit|Garage|Concession|Soci[ée]t[ée]|Maison|H[ôo]tel|Restaurant|Lyc[ée]e"
    r"|Coll[èe]ge|Stade|Rue|Avenue|Boulevard|Place|Fondation|Institut|Prix|Ets|Etablissements)[ \u00a0]$",
    re.I,
)

_CELL_L = re.compile(r"(?:[\t|;]|\\t)$")
_CELL_R = re.compile(r"[\t|;]|\\t")

_lock = threading.Lock()
_loaded: Optional[Tuple[FrozenSet[str], FrozenSet[str], FrozenSet[str], Dict[str, int], FrozenSet[str]]] = None


def norm(value: str) -> str:
    """Lowercase, accents stripped: « DUPONT », « Dupont » and « dupont » match."""
    flat = unicodedata.normalize("NFD", value)
    return "".join(c for c in flat if unicodedata.category(c) != "Mn").lower()


def _read(name: str) -> List[str]:
    path = _DATA / name
    if not path.is_file():
        return []
    opener = gzip.open if name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [ln.strip() for ln in handle if ln.strip() and not ln.startswith("#")]


def _data() -> Tuple[FrozenSet[str], FrozenSet[str], FrozenSet[str], Dict[str, int], FrozenSet[str]]:
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                first = frozenset(_read("prenoms.txt.gz"))
                ambiguous = frozenset(_read("prenoms-ambigus.txt"))
                family = frozenset(_read("noms.txt.gz"))
                rank = {word: i for i, word in enumerate(_read("mots.txt.gz"))}
                rare = frozenset(_read("prenoms-rares.txt.gz"))
                _loaded = (first | rare, ambiguous, family, rank, rare)
    return _loaded


def available() -> bool:
    return bool(_data()[0])


# A surname next to a clear first name is refused only when it is one of the
# most frequent words; alone or next to a doubtful first name, the bar is higher.
_RANK_NEAR_FIRST = 1500
# An ambiguous first name rarer than this as a word (« Ken », « Marty ») still
# makes a person when a word no dictionary knows follows it.
_RANK_DOUBT = 3000
# Beyond this rank the frequency lists hold more surnames than words.
_RANK_WORD = 20000
# A surname that is also a frequent word (« Petit ») is never masked alone.
_RANK_ALONE = 5000


class Names:
    """Dictionary-backed checks. `never` holds the words the user excluded."""

    def __init__(self, never: Optional[Set[str]] = None) -> None:
        self.first, self.ambiguous, self.family, self.rank, self.rare = _data()
        self.never: Set[str] = set(_STATIC) | {norm(w) for w in (never or ())}

    def is_first(self, word: str) -> bool:
        n = norm(word)
        return n in self.first and n not in self.never

    def clear_first(self, word: str) -> bool:
        n = norm(word)
        return n in self.first and n not in self.never and n not in self.ambiguous and n not in self.rare

    def common(self, n: str) -> bool:
        """A plain word of the language, not a name."""
        if n in self.ambiguous:
            return True
        if n in self.family or n in self.first:
            return False
        return self.rank.get(n, 10**9) < _RANK_WORD

    def _core(self, word: str) -> str:
        parts = _SPLIT.split(word)
        return norm(parts[-1] if len(parts) > 1 and _PARTICLE.match(parts[0]) else word)

    def family_ok(self, word: str, near_clear_first: bool) -> bool:
        n = self._core(word)
        if len(n) < 2 or n in self.never or n in _ORG:
            return False
        if "-" in n and any(part in self.rank and part not in self.family for part in n.split("-")):
            # « Vice-Président » is a title, not a double-barrelled surname.
            return False
        if not self.common(n):
            return True
        return near_clear_first and n not in self.ambiguous and self.rank.get(n, 10**9) >= _RANK_NEAR_FIRST

    # -- detection ---------------------------------------------------------

    def detect(self, text: str, known: Dict[str, str]) -> List[Span]:
        """Private text: find names, remember them in `known`, return spans."""
        found: List[Span] = []
        if not text:
            return found
        self._fields(text, found)
        self._pairs(text, found)
        self._cues(text, found)
        self._learn(found, known)
        found.extend(self.known_spans(text, known))
        return found

    def _fields(self, text: str, found: List[Span]) -> None:
        for m in _KV.finditer(text):
            raw = m.group(2)
            val = raw.strip()
            if not _NAMEISH.match(val):
                continue
            words = [w for w in _SPLIT.split(val) if w]
            if len(words) > 4 or any(norm(w) in self.never or norm(w) in _ORG for w in words):
                continue
            key = m.group(1)
            strict = not re.fullmatch(_K_LOOSE, key)
            has_first = any(self.clear_first(w) for w in words)
            if strict:
                if not (has_first or re.search(rf"[{_UP}]", val)):
                    continue
                if len(words) == 1 and not has_first and re.fullmatch(_K_FULL, key):
                    # A one-word display name that is no first name is a handle or a brand.
                    continue
                if all(self.common(norm(w)) and not self.is_first(w) for w in words):
                    continue
                if not has_first and not all(re.match(rf"[{_UP}]", w) or _PARTICLE.match(w) for w in words):
                    # « Todoist watcher »: a label, not a person.
                    continue
            elif not has_first or (len(words) == 1 and key.lower() not in {"author", "auteur", "from", "de", "to", "cc"}):
                continue
            elif not all(re.match(rf"[{_UP}]", w) or _PARTICLE.match(w) for w in words):
                continue
            start = m.start(2) + len(raw) - len(raw.lstrip())
            if re.fullmatch(_K_FIRST, key):
                found.append((start, start + len(val), "PRENOM", val))
            else:
                self._split(start, val, found)
        for m in _HDR.finditer(text):
            val = m.group(1)
            if all(norm(w) in self.never or self.common(norm(w)) and not self.is_first(w) for w in val.split()):
                continue
            self._split(m.start(1), val, found)

    def _split(self, start: int, val: str, found: List[Span]) -> None:
        """« Marie Dupont » in a field gives the same two tokens as in a sentence."""
        head, sep, tail = val.partition(" ")
        if sep and tail.strip() and self.is_first(head):
            found.append((start, start + len(head), "PRENOM", head))
            offset = len(val) - len(tail.lstrip())
            found.append((start + offset, start + len(val), "NOM", val[offset:]))
            return
        body, sep, last = val.rpartition(" ")
        if sep and body.isupper() and not last.isupper() and self.clear_first(last):
            found.append((start, start + len(body), "NOM", body))
            found.append((start + len(val) - len(last), start + len(val), "PRENOM", last))
            return
        found.append((start, start + len(val), "NOM", val))

    def _pairs(self, text: str, found: List[Span]) -> None:
        pos = 0
        while True:
            m = _FULL.search(text, pos)
            if m is None:
                break
            # A refused pair is retried from its second word: in « Ajoute Marie
            # Dupont », « Ajoute Marie » fails and « Marie Dupont » must be seen.
            pos = m.start(2)
            a, b, c = m.group(1), m.group(2), m.group(3)
            clear = self.clear_first(a)
            if (
                self.clear_first(b) and not self.is_first(a) and len(a) >= 4
                and self.family_ok(a, False)
                and (
                    a.isupper()
                    or (not b.isupper() and norm(a) in self.family)
                    # An unknown word before a first name is a surname only in a
                    # list or a table cell: « Montpellier Frédéric Marty » is not.
                    or (not b.isupper() and norm(a) not in self.rank and _CELL_L.search(text[max(0, m.start() - 2):m.start()])
                        and _CELL_R.match(text[m.end():m.end() + 2]))
                )
            ):
                # « DUPONT Marie », « Dupont Marie »
                found.append((m.start(1), m.end(1), "NOM", a))
                found.append((m.start(2), m.end(2), "PRENOM", b))
                pos = m.end(2)
                continue
            if not self.is_first(a):
                continue
            core = self._core(b)
            na = norm(a)
            doubtful = na not in _ORG and (
                na in self.rare or (na in self.ambiguous and self.rank.get(na, 10**9) >= _RANK_DOUBT)
            )
            if doubtful and self.is_first(a) and not b.isupper():
                # « Nick Fehler » yes, « Meta Tags » no: a rare first name needs
                # a surname or a word no dictionary knows after it.
                if core not in self.family and (core in self.rank or len(core) < 4 or core in _ORG):
                    continue
                clear = True
            elif not clear and (core not in self.family or self.is_first(b)):
                # « Avis Xavier », « Any HTTP » : two doubtful words prove nothing.
                continue
            if b.isupper() and not a.isupper() and (
                self.common(self._core(b))
                or (norm(b) not in self.family and (len(b) <= 3 or (len(b) == 4 and not re.search("[AEIOUY]", b))))
            ):
                # « Robinson RÉSERVER », « Thomas HTTP » : a button or an acronym.
                continue
            if _ORG_BEFORE.search(text[max(0, m.start() - 12):m.start()]):
                continue
            if self.family_ok(b, clear):
                found.append((m.start(1), m.end(1), "PRENOM", a))
                end = m.end(3) if c and self.family_ok(c, False) else m.end(2)
                found.append((m.start(2), end, "NOM", text[m.start(2):end]))
                pos = end

    def _cues(self, text: str, found: List[Span]) -> None:
        for m in _GREET.finditer(text):
            if self.is_first(m.group(1)):
                found.append((m.start(1), m.end(1), "PRENOM", m.group(1)))
        for m in _CIV.finditer(text):
            word = m.group(1)
            if self.family_ok(word, True) or self.clear_first(word):
                found.append((m.start(1), m.end(1), "NOM", word))
        for m in _SIGN.finditer(text):
            if not self.is_first(m.group(1)):
                continue
            found.append((m.start(1), m.end(1), "PRENOM", m.group(1)))
            if m.group(2) and self.family_ok(m.group(2), True):
                found.append((m.start(2), m.end(2), "NOM", m.group(2)))

    def _learn(self, found: List[Span], known: Dict[str, str]) -> None:
        for _start, _end, kind, val in found:
            for word in _SPLIT.split(val):
                n = norm(word)
                if len(n) < 3 or n in self.never or n in self.ambiguous or _PARTICLE.match(word):
                    continue
                if n in self.first:
                    # « Mme Martin » does not make every « Martin » a masked word.
                    if kind == "PRENOM":
                        known.setdefault(n, "PRENOM")
                elif len(n) >= 4 and not self.common(n) and self.rank.get(n, 10**9) >= _RANK_ALONE:
                    known.setdefault(n, "NOM")

    def known_spans(self, text: str, known: Dict[str, str]) -> List[Span]:
        """Names already seen in private text, standing alone, in any text."""
        if not known or not text:
            return []
        out: List[Span] = []
        for m in _TOKEN.finditer(text):
            kind = known.get(norm(m.group(1)))
            if kind:
                out.append((m.start(1), m.end(1), kind, m.group(1)))
        return out
