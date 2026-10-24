import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402

AT = chr(64)
MAIL = "jean" + AT + "example.com"


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
    masker._maps.clear()
    masker._maps["s1"] = table
    table.mask_text("jean" + AT + "example.com")
    text = masker.status_text()
    assert "jean" not in text
    assert "1 email" in text


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


def test_postal_code_city_not_masked_without_street(table):
    text = "Envoyer à 69003 Lyon demain."
    assert table.mask_text(text) == text


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


def test_long_text_is_masked_not_skipped(table):
    filler = "ligne neutre sans donnée\n" * 100_000
    text = filler + "contact " + MAIL + "\n" + filler
    assert len(text) > 2 * masker._CHUNK
    masked = table.mask_text(text)
    assert MAIL not in masked
    assert table.unmask_text(masked) == text


def test_email_regex_is_linear(table):
    import time

    start = time.time()
    table.mask_text("a-" * 40_000)
    assert time.time() - start < 1.0


def test_email_after_punctuation_still_found(table):
    for text in ("(" + MAIL + ")", "mailto:" + MAIL, "x-" + MAIL):
        assert MAIL not in table.mask_text(text)


def test_term_matches_whole_words_only(tmp_path):
    store = masker.TermStore(tmp_path / "termes.txt")
    store.add("Ana")
    masker._terms = store
    try:
        table = masker.PiiMap()
        masked = table.mask_text("Ana a fait une banane et une analyse")
        assert masked.startswith("[NOM_1] a fait")
        assert "banane" in masked and "analyse" in masked
    finally:
        masker._terms = masker.TermStore()


def test_literal_placeholder_in_text_is_not_restored_as_private(table):
    table.mask_text(MAIL)
    text = "le doc cite marie.dupont@exemple.fr comme exemple"
    masked = table.mask_text(text)
    assert masked != text
    assert table.unmask_text(masked) == text
    assert MAIL not in table.unmask_text(masked)


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "gho_" + "a" * 36,
        "sk_live_" + "a" * 24,
        "xoxb-1234567890-abcdefghij",
        "AIza" + "B" * 35,
        "sk-ant-api03-" + "a" * 30,
        "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20,
    ],
)
def test_api_key_variants(table, secret):
    masked = table.mask_text("clé " + secret + " fin")
    assert secret not in masked
    assert "[API_KEY_1]" in masked
    assert table.unmask_text(masked) == "clé " + secret + " fin"


def test_request_walks_system_tools_and_prompt():
    masker._maps.clear()
    request = {
        "model": "m",
        "system": "profil " + MAIL,
        "tools": [{"description": "mail " + MAIL}],
        "prompt": MAIL,
        "messages": [],
    }
    updated = masker.mask_llm_request(request, "s1")
    assert MAIL not in str(updated)
    assert updated["model"] == "m"


def test_unchanged_request_returns_same_object():
    masker._maps.clear()
    request = {"messages": [{"role": "user", "content": "bonjour"}]}
    assert masker.mask_llm_request(request, "s1") is request


def test_status_counts_every_session():
    masker._maps.clear()
    masker.mask_llm_request({"messages": [{"role": "user", "content": MAIL}]}, "sess-A")
    assert "1 email" in masker.status_text()
    assert "1 email" in masker.status_text("sess-A")


def test_systemd_unit_names_are_not_emails(table):
    for unit in ("getty" + AT + "tty1.service", "hermes-gateway" + AT + "aura.service", "user" + AT + "1000.service", "x" + AT + "y.timer"):
        text = "systemctl restart " + unit
        assert table.mask_text(text) == text


def test_real_email_still_masked_next_to_unit_name(table):
    text = "systemctl restart getty" + AT + "tty1.service puis écrire à " + MAIL
    masked = table.mask_text(text)
    assert MAIL not in masked
    assert "getty" + AT + "tty1.service" in masked
    assert table.unmask_text(masked) == text


def test_process_listing_is_not_an_address(table):
    for pid in ("1378" + "5", "2749" + "1", "6944" + "0"):
        for rest in ("Sat Oct 24 09:58:31 2026", "Wed Oct 21 13:17:50 2026", "Oct 24 2026"):
            text = pid + " " + rest
            assert table.mask_text(text) == text


def test_street_with_comma_is_masked(table):
    text = "Il habite 12, rue de la Paix."
    masked = table.mask_text(text)
    assert "rue de la Paix" not in masked
    assert table.unmask_text(masked) == text


def test_curly_apostrophe_street_is_masked(table):
    text = "Devant 3 rue de l\u2019\u00c9glise."
    masked = table.mask_text(text)
    assert "glise" not in masked
    assert table.unmask_text(masked) == text


def test_tool_output_street_shape_not_masked(table):
    for text in ("12 square meters of space", "12 route show default", "12 chemin de fer"):
        assert table.mask_text(text) == text


def test_known_placeholder_is_not_rewrapped(table):
    masked = table.mask_text(MAIL)
    token = masker._PLACEHOLDER_RE.search(masked).group(0)
    followup = "le modele ecrit " + token + " seul"
    assert table.mask_text(followup) == followup


def test_mask_text_is_idempotent(table):
    once = table.mask_text("contact " + MAIL + " et +33 6 12 34 56 78")
    assert table.mask_text(once) == once


def test_model_tool_args_keep_their_token(table):
    import json as _json

    masked = table.mask_text(MAIL)
    token = masker._PLACEHOLDER_RE.search(masked).group(0)
    request = {"messages": [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send", "arguments": _json.dumps({"to": token})}}]}]}
    updated = masker.mask_value(request, table)
    seen = _json.loads(updated["messages"][0]["tool_calls"][0]["function"]["arguments"])["to"]
    assert seen == token


def test_signed_thinking_block_is_untouched(table):
    block = {"type": "thinking", "thinking": "reponse a " + MAIL, "signature": "EqQBCkYIBxgC"}
    out = masker.mask_value([block], table)
    assert out[0] is block


def test_generic_iban_countries(table):
    for iban in ("BE68 5390 0754 7034", "DE89 3704 0044 0532013000", "CH93 0076 2011 6238 5295 7", "GB29 NWBK 6016 1331 9268 19", "LU28 0019 4006 4475 0000"):
        text = "IBAN " + iban + " svp"
        masked = table.mask_text(text)
        assert iban not in masked
        assert table.unmask_text(masked) == text


def test_invalid_iban_is_not_masked(table):
    for fake in ("BE68 5390 0754 7035", "XX00 1234 5678 9012"):
        assert table.mask_text("IBAN " + fake) == "IBAN " + fake


def test_credit_card_masked_with_luhn(table):
    text = "carte 4242 4242 4242 4242 fin"
    masked = table.mask_text(text)
    assert "4242" not in masked
    assert table.unmask_text(masked) == text
    assert table.mask_text("carte 1234 5678 9012 3456 fin") == "carte 1234 5678 9012 3456 fin"


def test_international_phones(table):
    for num in ("+1 415 555 0132", "+44 7911 123456", "+32 470 12 34 56", "+41 79 123 45 67", "+352 621 123 456"):
        text = "appel " + num + " ok"
        masked = table.mask_text(text)
        assert num not in masked
        assert table.unmask_text(masked) == text


def test_date_after_country_code_is_not_a_phone(table):
    assert table.mask_text("rendez-vous +33 2026-10-24 a Paris") == "rendez-vous +33 2026-10-24 a Paris"


def test_french_phone_with_zero_parenthesis(table):
    text = "numero +33 (0)6 12 34 56 78 et (+33) 6 12 34 56 78"
    masked = table.mask_text(text)
    assert "[PHONE_1]" in masked
    assert table.unmask_text(masked) == text


def test_new_secret_families(table):
    secrets = [
        "whsec_" + "a" * 24,
        "ntn_" + "a" * 30,
        "secret_" + "a" * 30,
        "pat" + "A1" * 7 + "." + "ab12" * 16,
        "123456789" + ":AA" + "Hx" * 17,
        "MTA1" + "x" * 20 + "." + "Gabc12" + "." + "y" * 27,
        "ya29." + "A1b2C3" * 8,
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY-----",
    ]
    for secret in secrets:
        text = "cle " + secret + " fin"
        masked = table.mask_text(text)
        assert secret not in masked, secret[:12]
        assert table.unmask_text(masked) == text


def test_status_shows_version_and_self_test():
    masker._maps.clear()
    text = masker.status_text()
    assert masker.VERSION in text
    assert "Auto-test : " in text
    assert "OK" in text


def test_drop_map_forgets_session():
    masker._maps.clear()
    masker.mask_llm_request({"messages": [{"role": "user", "content": MAIL}]}, "sess-drop")
    assert "1 email" in masker.status_text("sess-drop")
    masker.drop_map("sess-drop")
    assert "Rien de masqué" in masker.status_text("sess-drop")


def test_code_emails_are_not_masked(table):
    A = chr(64)
    for text in ("logo" + A + "2x.png", "icon" + A + "3x.webp", "git" + A + "github.com:Kavyrocom/repo.git", "doc" + A + "example.test", "a" + A + "b.invalid", "x" + A + "y.localhost"):
        assert table.mask_text("fichier " + text + " ok") == "fichier " + text + " ok"


def test_real_email_with_common_tld_still_masked(table):
    A = chr(64)
    for text in ("jean" + A + "exemple.fr", "jean" + A + "exemple.com", "jean" + A + "exemple.co.uk", "jean" + A + "exemple.mu"):
        masked = table.mask_text("contact " + text)
        assert text not in masked
