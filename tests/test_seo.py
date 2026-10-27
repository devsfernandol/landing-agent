from app import funnel, seo
from tests.fake_llm import LANDING_V1, STRATEGY


def test_optimize_is_idempotent_and_raises_score():
    before = seo.audit(LANDING_V1, "curso de excel para contadores")["score"]
    once = seo.optimize(LANDING_V1, STRATEGY, "https://x/p/a")
    twice = seo.optimize(once, STRATEGY, "https://x/p/a")
    assert twice.count("<title>") == 1 and twice.count("application/ld+json") == 1 and twice.count("la-consent") == 1
    assert seo.audit(twice, "curso de excel para contadores")["score"] > before


def test_variant_b_only_changes_h1():
    b = seo.make_variant(LANDING_V1, "Otro titular")
    assert seo.h1_text(b) == "Otro titular"
    assert b.replace("Otro titular", "") .count("<h2>") == LANDING_V1.count("<h2>")


def test_conversion_audit_respects_brief():
    c = seo.conversion_audit(LANDING_V1, STRATEGY, brief="sin garantia mencionada")
    by_id = {x["id"]: x for x in c["checks"]}
    assert by_id["form"]["ok"] and by_id["honesty"]["ok"]
    assert not by_id["price"]["ok"]  # 147 no aparece en la v1


def test_theme_inherits_landing_identity():
    t = funnel.theme(LANDING_V1)
    assert t["bg"] == "#fdfaf3" and t["text"] == "#1d1d1f" and t["accent"] == "#0f766e"
    assert "Inter" in t["body_font"] and "Fraunces" in t["head_font"]
