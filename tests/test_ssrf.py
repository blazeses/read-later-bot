import pytest
from app.extractors.ssrf import SSRFSecurityError, is_ip_allowed, validate_url_safe


def test_ip_whitelist_and_blacklist():
    # Forbidden / Private
    assert is_ip_allowed("127.0.0.1") is False
    assert is_ip_allowed("10.0.0.1") is False
    assert is_ip_allowed("192.168.1.1") is False
    assert is_ip_allowed("172.16.0.1") is False
    assert is_ip_allowed("169.254.169.254") is False
    assert is_ip_allowed("::1") is False

    # Allowed Public IPs
    assert is_ip_allowed("8.8.8.8") is True
    assert is_ip_allowed("1.1.1.1") is True


def test_validate_url_blocks_internal():
    with pytest.raises(SSRFSecurityError):
        validate_url_safe("http://127.0.0.1:8000/admin")

    with pytest.raises(SSRFSecurityError):
        validate_url_safe("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(SSRFSecurityError):
        validate_url_safe("ftp://example.com")
