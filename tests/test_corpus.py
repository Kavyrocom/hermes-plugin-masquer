"""Public, reproducible score for person-name detection.

The corpus is synthetic: invented people, usual sentences in French and English,
and as many lines with no person at all. The thresholds sit just under the score
measured at release, so a change that degrades detection fails the build.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402

CORPUS = ROOT / "tests" / "corpus" / "noms.tsv"
MIN_RECALL = 0.90
MAX_FALSE_LINES = 0.06


def load():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        text, _, names = line.partition("\t")
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        rows.append((text, [n for n in names.split(";") if n]))
    return rows


def score():
    found = expected = false_lines = clean = 0
    missed, wrong = [], []
    for text, names in load():
        masked = masker.PiiMap().mask_text(text)
        if not names:
            clean += 1
            if masked != text:
                false_lines += 1
                wrong.append(masked)
            continue
        for name in names:
            expected += 1
            if any(part in masked for part in name.split()):
                missed.append(name)
            else:
                found += 1
    return found / expected, false_lines / clean, missed, wrong


def test_corpus_is_balanced():
    rows = load()
    assert sum(1 for _, n in rows if n) >= 50 and sum(1 for _, n in rows if not n) >= 50


def test_name_detection_score():
    recall, false_rate, missed, wrong = score()
    assert recall >= MIN_RECALL, f"rappel {recall:.0%}, noms ratés : {missed}"
    assert false_rate <= MAX_FALSE_LINES, f"faux positifs {false_rate:.0%} : {wrong}"


if __name__ == "__main__":
    recall, false_rate, missed, wrong = score()
    print(f"Noms masqués : {recall:.0%}. Lignes sans personne masquées à tort : {false_rate:.0%}.")
    for name in missed:
        print("  raté :", name)
    for line in wrong:
        print("  à tort :", line)
