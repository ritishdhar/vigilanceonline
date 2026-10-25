from core.alert_dispatch import Incident, TelegramConfig, TelegramNotifier
from core.telegram_recipients import TelegramEnrollmentBot, TelegramRecipientStore


def test_invite_enrollment_is_expiring_and_idempotent(tmp_path):
    store = TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    expires = store.create_invite("valid_invite", now=100)

    assert expires == 100 + 3600
    assert store.enroll("valid_invite", "chat-1", "user-1", "alice", "Alice", now=101)
    assert store.enroll("valid_invite", "chat-1", "user-1", "alice", "Alice", now=102)
    assert store.active_chat_ids() == ["chat-1"]
    assert store.recipients()[0]["display_name"] == "Alice"

    assert not store.enroll("valid_invite", "chat-2", "user-2", now=3700)
    assert not store.enroll("unknown_invite", "chat-2", "user-2", now=101)


def test_invite_rotation_and_opt_out(tmp_path):
    store = TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    store.create_invite("first", now=100)
    store.create_invite("second", now=101)

    assert not store.enroll("first", "chat-1", "user-1", now=102)
    assert store.enroll("second", "chat-1", "user-1", now=102)
    assert store.unsubscribe("chat-1")
    assert store.active_chat_ids() == []
    assert store.recipients()[0]["active"] == 0


def test_telegram_start_and_stop_commands_opt_in(tmp_path, monkeypatch):
    store = TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    store.create_invite("join_me")
    bot = TelegramEnrollmentBot("not-a-real-token", store)
    messages = []
    monkeypatch.setattr(
        bot, "_send_message", lambda chat, text: messages.append((chat, text))
    )

    bot._handle_update(
        {
            "message": {
                "text": "/start join_me",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456, "username": "responder", "first_name": "Riya"},
            }
        }
    )
    assert store.active_chat_ids() == ["123"]
    assert "Send /stop" in messages[0][1]

    bot._handle_update(
        {
            "message": {
                "text": "/stop",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
            }
        }
    )
    assert store.active_chat_ids() == []


def test_non_private_telegram_chat_cannot_enroll(tmp_path):
    store = TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    store.create_invite("join_me", now=100)
    bot = TelegramEnrollmentBot("not-a-real-token", store)

    bot._handle_update(
        {
            "message": {
                "text": "/start join_me",
                "chat": {"id": -123, "type": "group"},
                "from": {"id": 456},
            }
        }
    )
    assert store.active_chat_ids() == []


def test_alert_dispatch_fans_out_text_only_to_enrolled_recipients(monkeypatch):
    class Store:
        def active_chat_ids(self):
            return ["chat-1", "chat-2"]

    calls = []

    def post(method, payload):
        calls.append((method, payload))
        return {"ok": True}

    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="TOKEN", cooldown_sec=0),
        recipient_store=Store(),
    )
    monkeypatch.setattr(notifier, "_post", post)
    incident = Incident("evt-1", "Possible fight detected", "FIGHT")

    assert notifier.maybe_notify(incident, jpeg=b"private-frame", now=100)
    assert [payload["chat_id"] for _, payload in calls] == ["chat-1", "chat-2"]
    assert all(method == "sendMessage" for method, _ in calls)
    assert not notifier.maybe_notify(incident, jpeg=b"private-frame", now=101)


def test_legacy_chat_keeps_images_while_enrolled_recipients_get_text(monkeypatch):
    class Store:
        def active_chat_ids(self):
            return ["enrolled-chat"]

    calls = []
    notifier = TelegramNotifier(
        TelegramConfig(
            enabled=True, bot_token="TOKEN", chat_id="legacy-chat", cooldown_sec=0
        ),
        recipient_store=Store(),
    )
    monkeypatch.setattr(
        notifier,
        "_send",
        lambda incident, jpeg: calls.append(("legacy-chat", jpeg)) or True,
    )
    monkeypatch.setattr(
        notifier,
        "_send_to_chat",
        lambda incident, jpeg, chat_id: calls.append((chat_id, jpeg)) or True,
    )

    assert notifier.maybe_notify(
        Incident("evt-legacy", "Possible fight detected", "FIGHT"),
        jpeg=b"legacy-frame",
        now=100,
    )
    assert calls == [("enrolled-chat", None), ("legacy-chat", b"legacy-frame")]


def test_fanout_retries_only_failed_recipient(monkeypatch):
    class Store:
        def active_chat_ids(self):
            return ["chat-1", "chat-2"]

    results = iter([True, False, True])
    calls = []
    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="TOKEN", cooldown_sec=60),
        recipient_store=Store(),
    )

    def send(incident, jpeg, chat_id):
        calls.append(chat_id)
        return next(results)

    monkeypatch.setattr(notifier, "_send_to_chat", send)
    incident = Incident("evt-2", "Possible fight detected", "FIGHT")

    assert not notifier.maybe_notify(incident, now=100)
    assert notifier.maybe_notify(incident, now=116)
    assert calls == ["chat-1", "chat-2", "chat-2"]


def test_unsubscribed_recipient_is_not_retried_or_left_pending(monkeypatch):
    class Store:
        chats = ["chat-1", "chat-2"]

        def active_chat_ids(self):
            return list(self.chats)

    store = Store()
    calls = []
    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="TOKEN", cooldown_sec=0),
        recipient_store=store,
    )

    def send(incident, jpeg, chat_id):
        calls.append(chat_id)
        return chat_id == "chat-1"

    monkeypatch.setattr(notifier, "_send_to_chat", send)
    incident = Incident("evt-unsubscribe", "Possible fight detected", "FIGHT")

    assert not notifier.maybe_notify(incident, now=100)
    store.chats = ["chat-1"]
    assert not notifier.maybe_notify(incident, now=116)
    assert not notifier.maybe_notify(incident, now=117)
    assert calls == ["chat-1", "chat-2"]


def test_enrollment_test_message_is_explicit_not_an_incident(tmp_path, monkeypatch):
    store = TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    store.create_invite("join_me", now=100)
    store.enroll("join_me", "chat-1", "user-1", now=101)
    bot = TelegramEnrollmentBot("not-a-real-token", store)
    calls = []
    monkeypatch.setattr(
        bot, "_send_message", lambda chat, text: calls.append((chat, text))
    )

    assert bot.send_test_alert() == 1
    assert calls[0][0] == "chat-1"
    assert "not a detected incident" in calls[0][1]


def test_rejected_telegram_tokens_do_not_leak_request_urls(tmp_path, monkeypatch):
    from urllib.error import HTTPError

    import pytest

    from core import telegram_recipients

    secret = "private-token-that-must-not-appear"
    bot = TelegramEnrollmentBot(
        secret, TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    )

    def rejected(request, **kwargs):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(telegram_recipients, "urlopen", rejected)
    with pytest.raises(telegram_recipients.TelegramAPIError) as error:
        bot._request("getMe")
    assert str(error.value) == "BOT_TOKEN_REJECTED"
    assert secret not in str(error.value)


def test_polling_does_not_replace_an_existing_webhook(tmp_path, monkeypatch):
    bot = TelegramEnrollmentBot(
        "test-token", TelegramRecipientStore(tmp_path / "recipients.sqlite3")
    )
    methods = []

    def request(method, payload=None):
        methods.append(method)
        return (
            {"username": "test_bot"}
            if method == "getMe"
            else {"url": "https://existing-service.example/webhook"}
        )

    monkeypatch.setattr(bot, "_request", request)
    monkeypatch.setattr(bot._stop, "wait", lambda seconds: bot._stop.set())
    bot._poll()
    assert methods == ["getMe", "getWebhookInfo"]
    assert bot.username is None
    assert bot.last_error == "WEBHOOK_ACTIVE"


def test_dashboard_sender_uses_the_live_enrollment_store(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import ui

    monkeypatch.setenv("VIGILANCE_TELEGRAM_BOT_TOKEN", "not-a-real-token")
    monkeypatch.setenv(
        "VIGILANCE_TELEGRAM_RECIPIENTS_DB", str(tmp_path / "recipients.sqlite3")
    )
    monkeypatch.setattr(TelegramEnrollmentBot, "start", lambda self: None)
    monkeypatch.setattr(ui, "pump_frames", lambda *args: None)
    monkeypatch.setattr(
        ui,
        "Pipeline",
        lambda **kwargs: SimpleNamespace(
            pair_adapter=SimpleNamespace(is_ready=True, head_trained=True)
        ),
    )
    monkeypatch.setattr(
        ui,
        "BoundedHTTPServer",
        lambda *args: SimpleNamespace(
            serve_forever=lambda: None, shutdown=lambda: None, server_close=lambda: None
        ),
    )
    from core import alert_dispatch

    original = alert_dispatch.TelegramNotifier
    captured = []

    def sender(config, recipient_store=None):
        notifier = original(config, recipient_store=recipient_store)
        captured.append(notifier)
        return notifier

    monkeypatch.setattr(alert_dispatch, "TelegramNotifier", sender)
    assert ui.main(["--synthetic"]) == 0
    notifier = captured[0]
    assert notifier.recipient_store is not None
    assert not notifier.ready
    notifier.recipient_store.create_invite("live_enrollment")
    assert notifier.recipient_store.enroll("live_enrollment", "new-chat", "new-user")
    assert notifier.ready
