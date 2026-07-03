from webcam_aggregator.models import Candidate, stable_id


def _c(page: str, angle: str | None = None, target: str = "x") -> Candidate:
    return Candidate(
        title="t",
        angle_key=angle,
        category=None,
        source="cxtvlive",
        source_page_url=page,
        target_url=target,
        predisc_key=None,
    )


def test_cosmetic_url_variants_same_id():
    a = stable_id(_c("https://www.cxtvlive.com/live-camera/foo"))
    b = stable_id(_c("https://WWW.cxtvlive.com/live-camera/foo/?utm_source=x"))
    c = stable_id(_c("http://www.cxtvlive.com/live-camera/foo"))
    assert a == b == c


def test_distinct_pages_and_angles_differ():
    assert stable_id(_c("https://s/a")) != stable_id(_c("https://s/b"))
    assert stable_id(_c("https://s/a", "0")) != stable_id(_c("https://s/a", "1"))


def test_id_independent_of_target_url():
    assert stable_id(_c("https://s/a", target="t1")) == stable_id(
        _c("https://s/a", target="t2")
    )
