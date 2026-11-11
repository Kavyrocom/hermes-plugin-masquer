import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import masker  # noqa: E402
import noms  # noqa: E402


@pytest.fixture(autouse=True)
def _names_on(monkeypatch):
    monkeypatch.setattr(masker, "_names_enabled", True)
    monkeypatch.setattr(masker, "_excluded", set())
    monkeypatch.setattr(masker, "_names", None)
    monkeypatch.setattr(masker, "_config_dir", None)


@pytest.fixture
def table():
    return masker.PiiMap()


def test_data_is_embedded():
    assert noms.available()


def test_full_name_roundtrip(table):
    text = "Rendez-vous avec Camille Lefebvre demain."
    masked = table.mask_text(text)
    assert "Camille" not in masked and "Lefebvre" not in masked
    assert "[PRENOM_1] [NOM_1]" in masked
    assert table.unmask_text(masked) == text


def test_same_person_same_token_whatever_the_case(table):
    masked = table.mask_text("Camille Lefebvre a payé. Facture au nom de LEFEBVRE Camille.")
    assert masked.count("[NOM_1]") == 2 and masked.count("[PRENOM_1]") == 2


def test_first_name_alone_is_masked_once_the_person_is_known(table):
    table.mask_text("Appel avec Camille Lefebvre.")
    assert table.mask_text("Camille rappelle jeudi.") == "[PRENOM_1] rappelle jeudi."


def test_first_name_alone_without_context_is_left(table):
    text = "Le prénom Camille est mixte."
    assert table.mask_text(text) == text


def test_greeting_and_signature(table):
    masked = table.mask_text("Bonjour Thomas,\nMerci pour le devis.\nCordialement,\nSophie Marchetti")
    assert "Thomas" not in masked and "Sophie" not in masked and "Marchetti" not in masked


def test_structured_fields(table):
    raw = json.dumps({"first_name": "Nadia", "last_name": "Benali", "status": "paid"})
    masked = table.mask_text(raw)
    assert "Nadia" not in masked and "Benali" not in masked
    assert json.loads(masked)["status"] == "paid"
    assert table.unmask_text(masked) == raw


def test_email_header(table):
    masked = table.mask_text("From: Hugo Vasseur <hugo" + chr(64) + "exemple.fr>")
    assert masked == "From: [PRENOM_1] [NOM_1] <[" + "EMAIL_1]>"


@pytest.mark.parametrize(
    "text",
    [
        "Bonjour Madame, merci pour votre retour.",
        "Any HTTP client able to reach the API.",
        "Le rachat de Pascal Moto en avril.",
        "Open Source Software for Marketing Teams",
        "Visual Studio Code et Google Chrome",
        "Mise à jour du Mardi 12 Novembre",
        "class Path Finder: pass",
        "/home/user/Camille Lefebvre/notes.md",
    ],
)
def test_no_false_positive(table, text):
    assert table.mask_text(text) == text


def test_common_word_surname_not_masked_alone(table):
    table.mask_text("Contrat signé par Julien Petit.")
    text = "Un petit budget suffit."
    assert table.mask_text(text) == text


def test_web_text_keeps_public_names(table):
    text = "Le ministre Bruno Retailleau a présenté le texte."
    assert table.mask_text(text, masker.KNOWN) == text


def test_private_wins_over_web(table):
    table.mask_text("Client : Bruno Retailleau, devis envoyé.")
    masked = table.mask_text("Bruno Retailleau a présenté le texte.", masker.KNOWN)
    assert "Retailleau" not in masked


def _request():
    return {
        "system": "You are an agent. Owner: Camille Lefebvre.",
        "messages": [
            {"role": "user", "content": "Cherche des infos sur le marché."},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{\"query\": \"marché\"}"}},
                {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"crm.csv\"}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "Interview de Mathilde Garnier et d'Antoine Roussel."},
            {"role": "tool", "tool_call_id": "c2", "content": "nom;statut\nAntoine Roussel;payé"},
        ],
    }


def test_request_provenance_openai_format():
    out = masker.mask_llm_request(_request(), "s-prov")
    web, private = out["messages"][2]["content"], out["messages"][3]["content"]
    assert "Mathilde Garnier" in web
    assert "Roussel" not in private
    # Seen in a private file later in the request: masked in the web result too.
    assert "Roussel" not in web
    # The system prompt is not scanned for new names.
    assert "Camille Lefebvre" in out["system"]
    masker.drop_map("s-prov")


def test_request_provenance_anthropic_format():
    request = {
        "messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "web_extract", "input": {"urls": ["https://exemple.fr"]}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Portrait de Mathilde Garnier."},
                {"type": "text", "text": "Ajoute Mathieu Collin au suivi."},
            ]},
        ]
    }
    out = masker.mask_llm_request(request, "s-anth")
    blocks = out["messages"][1]["content"]
    assert "Mathilde Garnier" in blocks[0]["content"]
    assert "Collin" not in blocks[1]["text"]
    masker.drop_map("s-anth")


def test_signed_thinking_untouched():
    block = {"type": "thinking", "thinking": "Camille Lefebvre a écrit.", "signature": "abc"}
    request = {"messages": [{"role": "assistant", "content": [block]},
                            {"role": "user", "content": "Camille Lefebvre a écrit."}]}
    out = masker.mask_llm_request(request, "s-think")
    assert out["messages"][0]["content"][0] == block
    masker.drop_map("s-think")


def test_tool_arguments_stay_valid_json_and_restore():
    table = masker.get_map("s-args")
    table.mask_text("Client : Camille Lefebvre")
    request = {"messages": [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "send", "arguments": json.dumps({"to": "Camille Lefebvre\nParis"})}},
    ]}]}
    out = masker.mask_llm_request(request, "s-args")
    args = json.loads(out["messages"][0]["tool_calls"][0]["function"]["arguments"])
    assert "Lefebvre" not in args["to"]
    assert masker.unmask_value(args, table) == {"to": "Camille Lefebvre\nParis"}
    masker.drop_map("s-args")


def test_names_off(table, monkeypatch):
    monkeypatch.setattr(masker, "_names_enabled", False)
    text = "Rendez-vous avec Camille Lefebvre."
    assert table.mask_text(text) == text


def test_exclude_word(table):
    masker.exclude_name("Lefebvre")
    assert "Lefebvre" in table.mask_text("Camille Lefebvre arrive.")


def test_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(masker, "_terms", masker._terms)
    monkeypatch.setattr(masker, "_web_tools", masker._web_tools)
    masker.set_terms_path(tmp_path / "termes.txt")
    masker.set_names_enabled(False)
    masker.exclude_name("Mistral")
    masker.set_terms_path(tmp_path / "termes.txt")
    assert not masker.names_enabled()
    assert "mistral" in masker._excluded
    masker.set_names_enabled(True)
    assert not (tmp_path / "noms-off").exists()
