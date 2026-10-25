"""Encrypted launcher credentials can be reused without exposing secrets."""

import pytest

from core import local_credentials


def test_absent_credentials_leave_manual_configuration_unchanged(tmp_path):
    assert local_credentials.load_telegram_credentials(tmp_path / "missing") == {}


@pytest.mark.skipif(
    local_credentials.os.name != "nt", reason="Windows credential format"
)
def test_saved_credentials_load_backend_bot_token_and_ignore_legacy_owner_token(
    tmp_path, monkeypatch
):
    path = tmp_path / "credentials.xml"
    path.write_text(
        '<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj><MS><SS N="BotToken">aa</SS><SS N="OwnerToken">bb</SS></MS></Obj></Objs>'
    )
    decrypted = []

    def decrypt(value):
        decrypted.append(value)
        return {"aa": "test-bot", "bb": "x" * 32}[value]

    monkeypatch.setattr(local_credentials, "_decrypt", decrypt)
    assert local_credentials.load_telegram_credentials(path) == {"bot": "test-bot"}
    assert decrypted == ["aa"]


@pytest.mark.skipif(
    local_credentials.os.name != "nt", reason="Windows credential format"
)
def test_invalid_credentials_do_not_expose_contents_in_error(tmp_path):
    path = tmp_path / "credentials.xml"
    path.write_text("private-test-value")
    with pytest.raises(ValueError, match="could not be loaded") as error:
        local_credentials.load_telegram_credentials(path)
    assert "private-test-value" not in str(error.value)
