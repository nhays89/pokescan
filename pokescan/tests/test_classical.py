import asyncio
import cv2, numpy as np, pytest
from pokescan.analysis.classical import ClassicalAnalyzer, W, H, CORNER_R
from pokescan.analysis.policy import QualityPolicy, Verdict
from pokescan.config.models import ClassicalConfig, PolicyConfig
from pokescan.domain.models import Listing

rng = np.random.default_rng(0)
LISTING = Listing(item_id="v1|1|0", title="t", price=1.0, currency="USD", url=None, image_url=None, seller=None)


def card_img(front=False, whiten=None):
    c = np.zeros((H, W, 3), np.uint8)
    border, center = ((40, 200, 230), (200, 160, 120)) if front else ((120, 50, 15), (190, 110, 40))
    c[:] = border
    c[90:790, 70:560] = center
    cv2.circle(c, (W // 2, H // 2), 120, (60, 60, 220), -1)
    for side, (ys, xs) in (whiten or {}).items():       # whitening: speckled pale pixels on the outer edge
        patch = c[ys, xs]
        m = rng.random(patch.shape[:2]) < 0.8
        patch[m] = (222, 224, 226)
        c[ys, xs] = patch
    return c


def canvas(card, glare=None, bg=(95, 120, 100), skew=1.0):
    cw, ch = 1100, 1300
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[240, 160], [860, 150 + 30 * skew], [850, 1070], [225, 1040 - 10 * skew]])
    M = cv2.getPerspectiveTransform(src, dst)
    bgimg = np.full((ch, cw, 3), bg, np.uint8)
    mask = np.zeros((H, W), np.uint8)                   # rounded card mask
    cv2.rectangle(mask, (CORNER_R, 0), (W - CORNER_R - 1, H - 1), 255, -1)
    cv2.rectangle(mask, (0, CORNER_R), (W - 1, H - CORNER_R - 1), 255, -1)
    for x, y in [(CORNER_R, CORNER_R), (W - CORNER_R - 1, CORNER_R), (CORNER_R, H - CORNER_R - 1), (W - CORNER_R - 1, H - CORNER_R - 1)]:
        cv2.circle(mask, (x, y), CORNER_R, 255, -1)
    wc = cv2.warpPerspective(card, M, (cw, ch))
    wm = cv2.warpPerspective(mask, M, (cw, ch)) > 127
    img = bgimg.copy(); img[wm] = wc[wm]
    if glare:                                           # list of (cx, cy, radius): blown-out soft blob
        a = np.zeros((ch, cw), np.float32)
        for cx, cy, r in glare:
            cv2.circle(a, (cx, cy), r, 1.0, -1)
        a = np.clip(cv2.GaussianBlur(a, (0, 0), 8) * 1.4, 0, 1)[..., None]
        img = (img * (1 - a) + 255 * a).astype(np.uint8)
    img = np.clip(img.astype(np.float32) + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)
    return img


def save(tmp_path, name, img):
    p = tmp_path / name
    cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return p


def run(paths, **cfg):
    rep = asyncio.run(ClassicalAnalyzer(ClassicalConfig(**cfg)).analyze(LISTING, paths))
    return rep, QualityPolicy(PolicyConfig()).evaluate(rep)


TOP_WHITE = {"top": (slice(0, 7), slice(60, 570))}
BOTTOM_WHITE = {"bottom": (slice(H - 7, H), slice(60, 570))}
TL_GLARE = [(262, 190, 95)]   # over the top-left corner, reaching into the card interior


def test_clean_back_passes(tmp_path):
    rep, res = run([save(tmp_path, "a.jpg", canvas(card_img()))])
    assert rep.back_visible and rep.scope == "back_only" and not rep.defects and not rep.unassessed_zones, rep.summary
    assert res.verdict == Verdict.PASS


def test_whitened_edge_fails(tmp_path):
    rep, res = run([save(tmp_path, "a.jpg", canvas(card_img(whiten=TOP_WHITE)))])
    assert any(d.location == "top" and d.severity in ("moderate", "major") for d in rep.defects), rep.summary
    assert res.verdict == Verdict.FAIL


def test_glare_alone_never_fails(tmp_path):
    rep, res = run([save(tmp_path, "a.jpg", canvas(card_img(), glare=TL_GLARE))])
    assert not rep.defects, rep.summary
    assert "top-left" in rep.unassessed_zones
    assert res.verdict != Verdict.FAIL


def test_glare_does_not_excuse_real_whitening(tmp_path):
    rep, res = run([save(tmp_path, "a.jpg", canvas(card_img(whiten=BOTTOM_WHITE), glare=TL_GLARE))])
    assert any(d.location == "bottom" for d in rep.defects), rep.summary
    assert res.verdict == Verdict.FAIL


def test_glare_zone_recovered_from_second_photo(tmp_path):
    a = save(tmp_path, "a.jpg", canvas(card_img(), glare=TL_GLARE))
    b = save(tmp_path, "b.jpg", canvas(card_img(), skew=0.5))
    rep, res = run([a, b])
    assert rep.unassessed_zones == [] and res.verdict == Verdict.PASS


def test_front_photo_goes_to_review(tmp_path):
    rep, res = run([save(tmp_path, "f.jpg", canvas(card_img(front=True)))])
    assert not rep.back_visible and res.verdict == Verdict.REVIEW


def test_no_card_goes_to_review(tmp_path):
    blank = np.full((900, 700, 3), 120, np.uint8)
    rep, res = run([save(tmp_path, "x.jpg", blank)])
    assert rep.image_quality == "insufficient" and res.verdict == Verdict.REVIEW


def test_debug_images_written(tmp_path):
    run([save(tmp_path, "a.jpg", canvas(card_img(whiten=BOTTOM_WHITE), glare=TL_GLARE))], debug_dir=str(tmp_path / "dbg"))
    assert list((tmp_path / "dbg").rglob("*_zones.jpg"))


def test_untrustworthy_outline_never_clears(tmp_path):
    """Glare on the same corner as edge whitening can break card localization. Whatever happens,
    the card must not be cleared as PASS."""
    rep, res = run([save(tmp_path, "a.jpg", canvas(card_img(whiten=TOP_WHITE), glare=TL_GLARE))])
    assert res.verdict != Verdict.PASS
