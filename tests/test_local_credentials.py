"""Encrypted launcher credentials can be reused without exposing secrets."""

import pytest

from core import local_credentials


def test_absent_credentials_leave_manual_configuration_unchanged(tmp_path):
    assert local_credentials.load_telegram_credentials(tmp_path / "missing") == {}


@pytest.mark.skipif(
    local_credentials.os.name != "nt", reason="Windows credential format"
)
def test_saved_credentials_read_secure_string_fields_only(tmp_path, monkeypatch):
    path = tmp_path / "credentials.xml"
    path.write_text(
        '<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj><MS><SS N="BotToken">aa</SS><SS N="OwnerToken">bb</SS></MS></Obj></Objs>'
    )
    monkeypatch.setattr(
        local_credentials,
        "_decrypt",
        lambda value: {"aa": "test-bot", "bb": "x" * 32}[value],
    )
    assert local_credentials.load_telegram_credentials(path) == {
        "bot": "test-bot",
        "owner": "x" * 32,
    }


@pytest.mark.skipif(
    local_credentials.os.name != "nt", reason="Windows credential format"
)
def test_invalid_credentials_do_not_expose_contents_in_error(tmp_path):
    path = tmp_path / "credentials.xml"
    path.write_text("private-test-value")
    with pytest.raises(ValueError, match="could not be loaded") as error:
        local_credentials.load_telegram_credentials(path)
    assert "private-test-value" not in str(error.value)
