import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402

TOKEN = "a9F3" * 8


def nir(body: str) -> str:
    digits = body.replace("2A", "19").replace("2B", "18")
    return body + "%02d" % (97 - int(digits) % 97)


@pytest.fixture
def table():
    return masker.PiiMap()


@pytest.mark.parametrize(
    "text, kept",
    [
        ("Authorization: Bea" + "rer " + TOKEN, "Authorization: Bearer [SECRET_1]"),
        ("GET /v1/x?api" + "_key=" + TOKEN + "&page=2", "GET /v1/x?api_key=[SECRET_1]&page=2"),
        ("STRIPE_SECRET" + "_KEY=" + TOKEN, "STRIPE_SECRET_KEY=[SECRET_1]"),
        ("TOKEN" + "=" + TOKEN, "TOKEN=[SECRET_1]"),
        ("export DB_PASS" + 'WORD="monsecretlong"', 'export DB_PASSWORD="[SECRET_1]"'),
        ('{"pass' + 'word": "Tr0ub4dor&3xyz"}', '{"password": "[SECRET_1]"}'),
        ("api" + "_key: 'abc123def456ghi'", "api_key: '[SECRET_1]'"),
    ],
)
def test_secret_value_masked_label_kept(table, text, kept):
    masked = table.mask_text(text)
    assert masked == kept
    assert table.unmask_text(masked) == text


def test_env_line_inside_serialized_tool_output(table):
    text = '{"output": "DEBUG=1\\nOPENAI_API' + "_KEY=" + "zq17" * 6 + '\\nPORT=80"}'
    masked = table.mask_text(text)
    assert "zq17" not in masked and "PORT=80" in masked
    assert table.unmask_text(masked) == text


@pytest.mark.parametrize(
    "text",
    [
        "DB_PASSWORD=${DB_PASSWORD}",
        'API_KEY = os.environ["OPENAI_API_KEY"]',
        "password = settings.password",
        'password: "<your-password>"',
        "SECRET_KEY=changeme",
        "MAX_TOKENS=4096",
        "https://exemple.fr/page?token=abc",
        "Authorization: Bearer $TOKEN",
        "Authorization: Bearer <token>",
        "the bearer of this letter",
        "api_key: str = None",
    ],
)
def test_not_a_secret(table, text):
    assert table.mask_text(text) == text


def test_same_secret_same_token(table):
    masked = table.mask_text("Bea" + "rer " + TOKEN + "\nMY_TOKEN=" + TOKEN)
    assert masked.count("[SECRET_1]") == 2 and "[SECRET_2]" not in masked


@pytest.mark.parametrize("body", ["1850775123456", "2940369123045", "185072A123456", "285122B001002"])
def test_nir_masked(table, body):
    text = "NSS : " + nir(body)
    assert table.mask_text(text) == "NSS : [NIR_1]"
    assert table.unmask_text("NSS : [NIR_1]") == text


def test_nir_with_spaces(table):
    n = nir("1850775123456")
    spaced = " ".join([n[0], n[1:3], n[3:5], n[5:7], n[7:10], n[10:13], n[13:]])
    assert table.mask_text(spaced) == "[NIR_1]"


def test_nir_wrong_key_left(table):
    n = nir("1850775123456")
    wrong = n[:13] + "%02d" % ((int(n[13:]) + 1) % 97)
    assert "[NIR_" not in table.mask_text("id " + wrong)


@pytest.mark.parametrize(
    "text, kept",
    [
        ("Né le 12/03/" + "1985 à Lyon", "Né le [NAISSANCE_1] à Lyon"),
        ("née le 3 mars " + "1990", "née le [NAISSANCE_1]"),
        ("Date de naissance : 21.07." + "1988", "Date de naissance : [NAISSANCE_1]"),
        ('"birth' + 'date": "1988-07-21"', '"birthdate": "[NAISSANCE_1]"'),
        ("Date of birth: 07/21/" + "1988", "Date of birth: [NAISSANCE_1]"),
    ],
)
def test_birth_date_masked(table, text, kept):
    masked = table.mask_text(text)
    assert masked == kept
    assert table.unmask_text(masked) == text


@pytest.mark.parametrize("text", ["créé le 12/03/2024", "Réunion du 3 mars 2026", "updated_at: 2026-11-11"])
def test_plain_date_left(table, text):
    assert table.mask_text(text) == text


def test_contact_names_two_columns():
    text = "Prénom;Nom;Ville\nCamille;Lefebvre;Lyon\nHugo;Vasseur;Nantes\n;;\n"
    assert masker.contact_names(text) == ["Camille Lefebvre", "Hugo Vasseur"]


def test_contact_names_full_name_column():
    text = "id,full_name,plan\n1,Nadia Benali,pro\n2,Sophie Marchetti,free\n"
    assert masker.contact_names(text) == ["Nadia Benali", "Sophie Marchetti"]


def test_contact_names_one_per_line():
    assert masker.contact_names("Nadia Benali\nSophie Marchetti\n") == ["Nadia Benali", "Sophie Marchetti"]


def test_contact_names_unknown_layout():
    assert masker.contact_names("a,b\n1,2\n") == []


def test_import_masks_lowercase_and_rare_names(tmp_path):
    store = masker.TermStore(tmp_path / "termes.txt")
    added, skipped = store.add_many(["Zorglub Krakowiak", "Zorglub Krakowiak", "Bob", "Nadia Benali"])
    assert (added, skipped) == (2, 2)
    assert masker.TermStore(tmp_path / "termes.txt").terms == ["Zorglub Krakowiak", "Nadia Benali"]
    assert store.spans("vu zorglub krakowiak hier") == [(3, 20)]
    assert store.spans("vu zorglub krakowiakova hier") == []


def test_term_index_follows_the_list(tmp_path):
    store = masker.TermStore(tmp_path / "termes.txt")
    assert store.spans("marie dupont") == []
    store.add("Marie Dupont")
    assert store.spans("marie dupont") == [(0, 12)]
    store.remove("Marie Dupont")
    assert store.spans("marie dupont") == []
