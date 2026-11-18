from jarvis.api.dashboard_auth import DashboardAuth, hash_password, verify_password


def configured_auth(clock=lambda: 1_700_000_000):
    return DashboardAuth(
        enabled=True,
        username="marcelo",
        password_hash=hash_password("uma-senha-forte", salt=b"0123456789abcdef"),
        session_secret="s" * 48,
        clock=clock,
    )


def test_password_hash_is_salted_and_rejects_wrong_password():
    encoded = hash_password("uma-senha-forte", salt=b"0123456789abcdef")

    assert verify_password("uma-senha-forte", encoded) is True
    assert verify_password("senha-incorreta", encoded) is False
    assert "uma-senha-forte" not in encoded


def test_signed_session_rejects_tampering_and_expiration():
    now = [1_700_000_000]
    auth = configured_auth(clock=lambda: now[0])
    token, issued = auth.issue_session()

    assert auth.read_session(token) == issued
    assert auth.read_session(token + "x") is None
    assert auth.read_session("%%%.%%%") is None
    now[0] += auth.session_ttl_seconds + 1
    assert auth.read_session(token) is None


def test_login_rate_limit_recovers_after_window():
    now = [1_700_000_000]
    auth = configured_auth(clock=lambda: now[0])

    for _ in range(auth.max_login_failures):
        assert auth.login_allowed("192.168.1.20") is True
        auth.record_login_failure("192.168.1.20")
    assert auth.login_allowed("192.168.1.20") is False

    now[0] += auth.login_window_seconds + 1
    assert auth.login_allowed("192.168.1.20") is True


def test_configuration_requires_hash_and_long_session_secret():
    auth = DashboardAuth(
        enabled=True,
        username="marcelo",
        password_hash="",
        session_secret="short",
    )

    assert auth.configured is False
