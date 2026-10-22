import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402


@pytest.fixture
def table():
    item = masker.PiiMap()
    return item


def test_email_roundtrip(table):
    text = "Écris à marie.dupont@exemple.fr demain."
    masked = table.mask_text(text)
    assert "marie.dupont@exemple.fr" not in masked
    assert "[EMAIL_1]" in masked
    assert table.unmask_text(masked) == text


def test_same_value_same_placeholder(table):
    a = table.mask_text("a@x.fr et encore a@x.fr")
    assert a.count("[EMAIL_1]") == 2
    assert "[EMAIL_2]" not in a


def test_french_mobile(table):
    text = "Appelle le 06 12 34 56 78 ce soir."
    masked = table.mask_text(text)
    assert "06 12 34 56 78" not in masked
    assert "[PHONE_1]" in masked
    assert table.unmask_text(masked) == text


def test_french_plus_33(table):
    text = "Numéro : +33612345678"
    masked = table.mask_text(text)
    assert "+33612345678" not in masked
    assert table.unmask_text(masked) == text


def test_iban(table):
    text = "IBAN FR76 3000 6000 0112 3456 7890 189"
    masked = table.mask_text(text)
    assert "FR76" not in masked
    assert "[IBAN_1]" in masked
    assert table.unmask_text(masked) == text


def test_discord_snowflake_not_phone(table):
    text = "channel 1559133646193959035"
    assert table.mask_text(text) == text


def test_nested_unmask_for_tools(table):
    table.mask_text("contact@kavyro.com")
    args = {"command": "echo [EMAIL_1]"}
    restored = masker.unmask_value(args, table)
    assert restored["command"] == "echo contact@kavyro.com"


def test_llm_request_masks_messages(table):
    masker._maps.clear()
    masker._maps["_default"] = table
    request = {
        "model": "grok-4.6",
        "messages": [{"role": "user", "content": "mail: test@kavyro.com"}],
    }
    updated = masker.mask_llm_request(request)
    assert "test@kavyro.com" not in str(updated["messages"])
    assert "[EMAIL_1]" in updated["messages"][0]["content"]
    assert updated["model"] == "grok-4.6"


def test_status_does_not_leak_values(table):
    table.mask_text("secret@kavyro.com")
    text = masker.status_text()
    assert "secret@kavyro.com" not in text
    assert "email" in text


def test_on_demand_works_when_auto_off(table):
    table.enabled = False
    masked = table.mask_text("marie.dupont@exemple.fr")
    assert "[EMAIL_1]" in masked
    assert "marie.dupont@exemple.fr" not in masked


def test_auto_off_skips_llm_request(table):
    table.enabled = False
    masker._maps.clear()
    masker._maps["_default"] = table
    request = {"messages": [{"role": "user", "content": "mail: test@kavyro.com"}]}
    updated = masker.mask_llm_request(request)
    assert updated is request
    assert "test@kavyro.com" in updated["messages"][0]["content"]


def test_french_address(table):
    text = "Il habite 12 rue de la Paix depuis 2020."
    masked = table.mask_text(text)
    assert "12 rue de la Paix" not in masked
    assert "[ADDRESS_1]" in masked
    assert "depuis 2020" in masked
    assert table.unmask_text(masked) == text


def test_postal_code_city(table):
    text = "Envoyer à 69003 Lyon demain."
    masked = table.mask_text(text)
    assert "69003 Lyon" not in masked
    assert "[ADDRESS_1]" in masked


def test_bare_paris_not_address(table):
    text = "On se voit à Paris demain."
    assert table.mask_text(text) == text


def test_named_term_and_never_david(tmp_path):
    store = masker.TermStore(tmp_path / "termes.txt")
    masker._terms = store
    assert "courant" in store.add("David").lower()
    assert "Ajouté" in store.add("Marie Dupont")
    table = masker.PiiMap()
    text = "Appelle Marie Dupont et David ce soir."
    masked = table.mask_text(text)
    assert "Marie Dupont" not in masked
    assert "David" in masked
    assert "[NOM_1]" in masked


def test_tool_call_arguments_stay_valid_json(table):
    import json

    command = "x = 1\n\x40pytest.fixture\ndef t(): pass\nmail jean\x40example.com"
    request = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "terminal", "arguments": json.dumps({"command": command})}}
                ],
            }
        ]
    }
    masked = masker.mask_value(request, table)
    raw = masked["messages"][0]["tool_calls"][0]["function"]["arguments"]
    decoded = json.loads(raw)
    assert "\n\x40pytest.fixture" in decoded["command"]
    assert "jean\x40example.com" not in decoded["command"]
    assert masker.unmask_value(decoded, table)["command"] == command
