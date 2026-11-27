import pytest

from webcam_aggregator.hosts import on_host


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x",
        "https://www.example.com/x",
        "http://a.b.example.com",
        "//www.example.com/embed",  # protocol-relative embed
        "https://example.com./x",  # a trailing dot names the same host
        "https://EXAMPLE.com/x",
    ],
)
def test_on_host_takes_the_host_and_its_subdomains(url: str) -> None:
    assert on_host(url, "example.com")


@pytest.mark.parametrize(
    "url",
    [
        "https://badexample.com/x",
        "https://example.com.evil.test/x",
        "https://evil.test/?u=https://example.com/x",
        "http://[bad/x",  # unparseable: on no host, never an error
        "",
    ],
)
def test_on_host_rejects_every_other_host(url: str) -> None:
    assert not on_host(url, "example.com")


def test_on_host_takes_several_domains() -> None:
    assert on_host("https://b.test/x", "a.test", "b.test")
    assert not on_host("https://c.test/x", "a.test", "b.test")
