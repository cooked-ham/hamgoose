from hamgoose import redact


def test_bearer():
    out = redact.redact("header Authorization: Bearer abcdef123456789")
    assert "abcdef123456789" not in out
    assert "REDACTED" in out


def test_api_key():
    out = redact.redact("api_key: secretvalue12345")
    assert "secretvalue12345" not in out


def test_sk_key():
    out = redact.redact("token sk-abcdefghij1234567890ZZ")
    assert "abcdefghij1234567890ZZ" not in out


def test_aws():
    out = redact.redact("aws AKIAIOSFODNN7EXAMPLE key")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_plain_unchanged():
    assert redact.redact("hello world") == "hello world"
