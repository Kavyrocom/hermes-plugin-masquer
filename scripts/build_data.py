"""Rebuild the lists embedded in data/ from their public sources.

    python scripts/build_data.py <folder holding the downloaded sources>

Sources (see README, « Données embarquées »):
  nat.zip                          INSEE, Fichier des prénoms (Licence Ouverte 2.0)
  common-forenames-by-country.csv  sigpwned/popular-names-by-country-dataset (CC0-1.0)
  common-surnames-by-country.csv   same
  fr_50k.txt, en_50k.txt           hermitdave/FrequencyWords, 2018 (MIT)

data/prenoms-ambigus.txt is hand-maintained; this script adds the rare first names
that are frequent words and drops the words that are no first name any more.
"""

import collections
import csv
import gzip
import io
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(__file__).resolve().parents[1] / "data"
WORD = re.compile(r"^[a-z]+(?:-[a-z]+)?$")
MIN_BIRTHS = 100   # INSEE: births since 1900 with this first name
SURE_BIRTHS = 300  # below, the first name is « rare »: it needs an unknown word after it
MAX_RANK = 50000   # frequency lists: keep the most frequent words of each language
RARE_BIRTHS, AUTO_RANK = 2000, 8000


def norm(value: str) -> str:
    flat = unicodedata.normalize("NFD", value)
    return "".join(c for c in flat if unicodedata.category(c) != "Mn").lower()


def keep(word: str) -> bool:
    return len(word) >= 3 and bool(WORD.match(word))


births: collections.Counter = collections.Counter()
with zipfile.ZipFile(SRC / "nat.zip") as archive:
    handle = io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="utf-8")
    next(handle)
    for line in handle:
        cols = line.rstrip("\n").split(";")
        if len(cols) == 4 and cols[1] and not cols[1].startswith("_"):
            births[norm(cols[1])] += int(cols[3])
first = {name for name, n in births.items() if n >= MIN_BIRTHS and keep(name)}
rare = {name for name in first if births[name] < SURE_BIRTHS}

with open(SRC / "common-forenames-by-country.csv", encoding="utf-8-sig") as handle:
    for row in csv.DictReader(handle):
        for part in row["Romanized Name"].split():
            if keep(norm(part)):
                first.add(norm(part))
                rare.discard(norm(part))

family = set()
with open(SRC / "common-surnames-by-country.csv", encoding="utf-8-sig") as handle:
    for row in csv.DictReader(handle):
        name = norm(row["Romanized Name"].strip())
        if keep(name):
            family.add(name)

rank: dict = {}
for name in ("fr_50k.txt", "en_50k.txt"):
    with open(SRC / name, encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            if i >= MAX_RANK:
                break
            word = norm(line.split()[0])
            if len(word) >= 2 and WORD.match(word):
                rank[word] = min(rank.get(word, i), i)
words = [w for w, _ in sorted(rank.items(), key=lambda kv: (kv[1], kv[0]))]


def write(name: str, lines: list) -> None:
    with open(OUT / name, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as out:
        out.write(("\n".join(lines) + "\n").encode("utf-8"))


write("prenoms.txt.gz", sorted(first - rare))
write("prenoms-rares.txt.gz", sorted(rare))
write("noms.txt.gz", sorted(family))
write("mots.txt.gz", words)

path = OUT / "prenoms-ambigus.txt"
lines = path.read_text(encoding="utf-8").splitlines()
head = [ln for ln in lines if ln.startswith("#")]
# A rare first name that is also a frequent word is ambiguous by construction.
auto = {name for name in first if births.get(name, 0) < RARE_BIRTHS and rank.get(name, MAX_RANK) < AUTO_RANK} - rare
body = sorted(({ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")} | auto) & (first | family))
path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
print(f"rares {len(rare)}  prenoms {len(first)}  noms {len(family)}  mots {len(words)}  ambigus {len(body)}")
