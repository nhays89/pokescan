"""Classical-CV analyzer: judges whitening on the BACK of a card. No ML, no API cost.

Pipeline per photo: find card -> perspective-warp to a fixed 630x880 canvas -> decide if it is the
back (blue border ring) -> build an edge band split into 8 zones (4 edges, 4 corners) ->
mask out flash glare -> score the fraction of "whitish" pixels per zone.

Design rule: glare must never be the reason a card is rejected. Glare pixels are excluded from
scoring; zones mostly hidden by glare are reported as UNASSESSED (not as defects); and zone
results are merged across all back photos, since glare moves between shots."""
from __future__ import annotations
import asyncio, logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable
import cv2
import numpy as np
from pokescan.config.models import ClassicalConfig
from pokescan.domain.models import Listing
from .base import ConditionReport, Defect

log = logging.getLogger(__name__)
W, H = 630, 880                      # 2.5 x 3.5 in at 252 px/in
CORNER_R = 30
ZONES = ["top", "bottom", "left", "right", "top-left", "top-right", "bottom-left", "bottom-right"]
SEV_ORDER = ["none", "minor", "moderate", "major"]


# ---------- geometry ----------
@lru_cache(maxsize=1)
def _dist_map() -> np.ndarray:
    """Distance (px) from each pixel inside the rounded card rectangle to its outline."""
    m = np.zeros((H, W), np.uint8)
    cv2.rectangle(m, (CORNER_R, 0), (W - CORNER_R - 1, H - 1), 255, -1)
    cv2.rectangle(m, (0, CORNER_R), (W - 1, H - CORNER_R - 1), 255, -1)
    for cx, cy in [(CORNER_R, CORNER_R), (W - CORNER_R - 1, CORNER_R),
                   (CORNER_R, H - CORNER_R - 1), (W - CORNER_R - 1, H - CORNER_R - 1)]:
        cv2.circle(m, (cx, cy), CORNER_R, 255, -1)
    m = cv2.copyMakeBorder(m, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)  # image border must count as outside
    return cv2.distanceTransform(m, cv2.DIST_L2, 5)[1:-1, 1:-1]


@lru_cache(maxsize=8)
def _zone_labels(lo: int, hi: int) -> np.ndarray:
    """int8 map: -1 = not in band, 0..7 = index into ZONES."""
    dist = _dist_map()
    ring = (dist >= lo) & (dist < hi)
    ys, xs = np.mgrid[0:H, 0:W]
    cw, ch = int(0.12 * W), int(0.085 * H)
    l, r, t, b = xs < cw, xs >= W - cw, ys < ch, ys >= H - ch
    lab = np.full((H, W), -1, np.int8)
    side = np.argmin(np.stack([ys, H - 1 - ys, xs, W - 1 - xs]), axis=0)  # 0 top,1 bottom,2 left,3 right
    for i in range(4):
        lab[ring & (side == i)] = i
    for idx, m in zip(range(4, 8), [t & l, t & r, b & l, b & r]):
        lab[ring & m] = idx
    return lab


def _order(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def _fit_pass(quad: np.ndarray, pts: np.ndarray, tol_frac: float, outer_px: float | None = None):
    """One pass: fit a line to the contour points near each side's middle 60%, intersect neighbours.
    Returns (new_quad, residuals_ok) or None if a side has too few points."""
    lines, ok = [], True
    cen = quad.mean(axis=0)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1:
            return None
        u = d / L
        n = np.array([-u[1], u[0]], np.float32)
        rel = pts - a
        t, off = rel @ u / L, np.abs(rel @ n)
        keep = (t > 0.2) & (t < 0.8) & (off < max(10.0, tol_frac * L))
        if outer_px is not None:
            # Wear/whitening only ever shrinks the visible card, so the true edge is the OUTERMOST
            # contour. Drop points lying more than outer_px inside the current line.
            n_out = n if float((a + b) / 2 @ n - cen @ n) > 0 else -n
            keep &= (rel @ n_out) > -outer_px
        sel = pts[keep]
        if len(sel) < 20:
            return None
        vx, vy, x0, y0 = cv2.fitLine(sel, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        p0, dv = np.array([x0, y0], np.float32), np.array([vx, vy], np.float32)
        resid = np.abs((sel - p0) @ np.array([-dv[1], dv[0]], np.float32))
        ok &= bool((resid < 4.0).mean() >= 0.8)
        lines.append((p0, dv))
    out = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        t = np.linalg.solve(A, p2 - p1)
        out.append(p1 + t[0] * d1)
    return np.array(out, np.float32), ok


def _refine(quad: np.ndarray, pts: np.ndarray):
    """A 4-point polygon fit cuts off the rounded corners and is thrown by glare near a corner.
    Re-fit straight sides with tightening tolerances. Returns (quad, ok); ok=False means the outline
    could not be trusted (so the caller must not score whitening from it)."""
    ok = False
    for tol, outer in ((0.08, None), (0.04, None), (0.02, 3.0), (0.02, 2.0), (0.02, 2.0)):
        res = _fit_pass(quad, pts, tol, outer)
        if res is None:
            return quad, False
        quad, ok = res
    return quad, ok


def find_card(img: np.ndarray) -> np.ndarray | None:
    """Return the 4 card corners (tl,tr,br,bl; portrait) in img coordinates, or None."""
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else img
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, 40, 120)
    # Ignore edges created by flash glare (a blown-out blob's own boundary is not the card outline).
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    blown = ((hsv[..., 2] >= 245) & (hsv[..., 1] <= 40)).astype(np.uint8)
    blown = cv2.morphologyEx(blown, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    if blown.any():
        blown = cv2.dilate(blown, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
        edges[blown > 0] = 0
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    img_area = small.shape[0] * small.shape[1]
    best, best_area = None, 0.0
    for c in cnts:
        hull = cv2.convexHull(c)
        ha = cv2.contourArea(hull)
        if ha < 0.12 * img_area or ha > 0.995 * img_area:
            continue
        peri, quad = cv2.arcLength(hull, True), None
        for eps in (0.01, 0.02, 0.03, 0.04):
            ap = cv2.approxPolyDP(hull, eps * peri, True)
            if len(ap) == 4:
                quad = ap
                break
        if quad is None:
            quad = cv2.boxPoints(cv2.minAreaRect(hull))
        quad = _order(quad)
        wd = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
        ht = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
        if min(wd, ht) < 1 or not (0.62 <= min(wd, ht) / max(wd, ht) <= 0.80):
            continue
        qa = cv2.contourArea(quad)
        if qa < 1 or ha / qa > 1.15 or ha / qa < 0.85:   # must be rectangle-like
            continue
        quad, fit_ok = _refine(quad, c.reshape(-1, 2).astype(np.float32))
        if not fit_ok:
            continue
        if wd > ht:                                        # landscape -> rotate to portrait
            quad = np.roll(quad, -1, axis=0)
        if ha > best_area:
            best, best_area = quad, ha
    return None if best is None else best / scale


# ---------- per-photo analysis ----------
@dataclass
class ZoneResult:
    score: float | None            # fraction of whitish pixels among usable ones; None = not assessed
    confidence: float = 0.0
    glare_frac: float = 0.0
    note: str = ""


@dataclass
class PhotoAnalysis:
    found: bool = False
    is_back: bool = False
    back_score: float = 0.0
    blurry: bool = False
    bg_bright: bool = False
    zones: dict[str, ZoneResult] = field(default_factory=dict)


def _glare_mask(V, S, dist, cfg: ClassicalConfig) -> np.ndarray:
    core = ((V >= cfg.glare_v) & (S <= cfg.glare_s)).astype(np.uint8)
    k = cfg.glare_min_px
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    glare = np.zeros_like(core)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < cfg.glare_min_area:
            continue
        comp = lab == i
        if dist[comp].max() < cfg.glare_interior_px:   # hugs the edge: that's wear, not glare
            continue
        glare[comp] = 1
    if glare.any() and cfg.glare_halo_px > 0:
        g = 2 * cfg.glare_halo_px + 1
        glare = cv2.dilate(glare, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g)))
    return glare.astype(bool)


def analyze_photo(img: np.ndarray, cfg: ClassicalConfig, debug_prefix: Path | None = None) -> PhotoAnalysis:
    out = PhotoAnalysis()
    quad = find_card(img)
    if quad is None:
        _save(debug_prefix, "found", _label(img.copy(), "NO CARD FOUND"))
        return out
    out.found = True
    dst = np.array([[0, 0], [W, 0], [W, H], [0, H]], np.float32)
    warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(quad.astype(np.float32), dst), (W, H))
    dist = _dist_map()

    # is it the back? (blue border ring)
    hsv = cv2.cvtColor(cv2.medianBlur(warped, 3), cv2.COLOR_BGR2HSV)
    Hh, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    ring = (dist >= 0.04 * W) & (dist <= 0.12 * W)
    blue = (Hh >= 95) & (Hh <= 135) & (S > 70) & (V > 25)
    out.back_score = float(blue[ring].mean())
    out.is_back = out.back_score >= cfg.back_blue_min

    # photo-quality signals
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    out.blurry = float(cv2.Laplacian(gray, cv2.CV_64F).var()) < cfg.blur_min
    q = quad.astype(np.int32)
    qmask = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(qmask, q, 255)
    outer = cv2.dilate(qmask, np.ones((41, 41), np.uint8)) & ~qmask
    if outer.any():
        ihsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        o = outer.astype(bool)
        out.bg_bright = bool(((ihsv[..., 2][o] > 170) & (ihsv[..., 1][o] < 60)).mean() > 0.5)

    if not out.is_back:
        _save(debug_prefix, "found", _label(_draw_quad(img, quad), f"NOT BACK (blue={out.back_score:.2f})"))
        return out

    # whitening / glare
    ref = (dist >= 0.06 * W) & (dist < 0.12 * W)
    ref_s, ref_v = float(np.median(S[ref])), float(np.median(V[ref]))
    whitish = (S < min(cfg.white_s_max, 0.45 * ref_s)) & (V > max(cfg.white_v_min, ref_v + 35))
    glare = _glare_mask(V, S, dist, cfg)
    labels = _zone_labels(cfg.inset_px, cfg.inset_px + int(cfg.band_pct * W))
    t_mod = cfg.whitening_thresholds[1]
    base_conf = 0.92 - (0.2 if out.blurry else 0) - (0.3 if out.bg_bright else 0)
    for zi, name in enumerate(ZONES):
        zm = labels == zi
        total = int(zm.sum())
        usable = zm & ~glare
        gfrac = 1 - usable.sum() / max(total, 1)
        if gfrac > cfg.glare_zone_max:
            out.zones[name] = ZoneResult(None, 0.0, gfrac, "glare")
            continue
        sc = float(whitish[usable].mean())
        wz = whitish & usable
        blown = float(((V >= 248) & wz).sum() / max(wz.sum(), 1))
        if sc >= t_mod and blown > 0.5:                # looks like blown-out glare, not paper-white wear
            out.zones[name] = ZoneResult(None, 0.0, gfrac, "possible glare")
            continue
        out.zones[name] = ZoneResult(sc, float(np.clip(base_conf - 0.5 * gfrac, 0.05, 0.95)), gfrac)

    if debug_prefix is not None:
        _save(debug_prefix, "found", _label(_draw_quad(img, quad), f"BACK (blue={out.back_score:.2f})"))
        vis = warped.copy()
        vis[whitish & (labels >= 0)] = (0, 0, 255)                       # red = counted as whitish
        vis[glare] = (0.55 * vis[glare] + 0.45 * np.array([255, 255, 0])).astype(np.uint8)  # cyan tint = glare
        vis[(labels >= 0) & ~whitish & ~glare] = (0.75 * vis[(labels >= 0) & ~whitish & ~glare]
                                                  + 0.25 * np.array([0, 255, 0])).astype(np.uint8)
        cv2.putText(vis, "red=whitish cyan=glare green=band", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        for zi, name in enumerate(ZONES):
            ys, xs = np.nonzero(labels == zi)
            if len(xs) == 0:
                continue
            z = out.zones[name]
            txt = f"{z.score:.2f}" if z.score is not None else z.note
            cx, cy = int(np.clip(xs.mean(), 60, W - 60)), int(np.clip(ys.mean(), 50, H - 20))
            cv2.putText(vis, txt, (cx - 25, cy + 40 if cy < H / 2 else cy - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        _save(debug_prefix, "zones", vis)
    return out


def _draw_quad(img, quad):
    out = img.copy()
    cv2.polylines(out, [quad.astype(np.int32)], True, (0, 255, 0), max(2, img.shape[1] // 300))
    return out


def _label(img, text):
    cv2.putText(img, text, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, max(0.8, img.shape[1] / 1200), (0, 255, 255), 3)
    return img


def _save(prefix: Path | None, kind: str, img: np.ndarray) -> None:
    if prefix is None:
        return
    prefix.parent.mkdir(parents=True, exist_ok=True)
    scale = 1000 / max(img.shape[:2]) if max(img.shape[:2]) > 1000 else 1
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imwrite(f"{prefix}_{kind}.jpg", img)


# ---------- listing-level analyzer ----------
def _severity(score: float, th: tuple[float, float, float]) -> str:
    return "none" if score < th[0] else "minor" if score < th[1] else "moderate" if score < th[2] else "major"


class ClassicalAnalyzer:
    """Implements ConditionAnalyzer. cfg_provider is called per listing, so YAML edits hot-reload."""

    def __init__(self, cfg_provider: Callable[[], ClassicalConfig] | ClassicalConfig):
        self._cfg = cfg_provider if callable(cfg_provider) else (lambda: cfg_provider)

    async def analyze(self, listing: Listing, images: list[Path]) -> ConditionReport:
        return await asyncio.to_thread(self._analyze_sync, listing, images, self._cfg())

    def _analyze_sync(self, listing: Listing, images: list[Path], cfg: ClassicalConfig) -> ConditionReport:
        safe = listing.item_id.replace("|", "_").replace("/", "_")
        photos: list[PhotoAnalysis] = []
        for i, path in enumerate(images):
            img = cv2.imread(str(path))
            if img is None:
                photos.append(PhotoAnalysis())
                continue
            prefix = Path(cfg.debug_dir) / safe / str(i) if cfg.debug_dir else None
            photos.append(analyze_photo(img, cfg, prefix))

        backs = [p for p in photos if p.is_back]
        found_any = any(p.found for p in photos)
        defects: list[Defect] = []
        unassessed: list[str] = []
        worst = 0
        if backs:
            for name in ZONES:
                obs = [(p.zones[name], p) for p in backs if name in p.zones and p.zones[name].score is not None]
                if not obs:
                    unassessed.append(name)
                    continue
                z, _ = max(obs, key=lambda o: o[0].score)        # worst usable observation, conservatively
                sev = _severity(z.score, cfg.whitening_thresholds)
                worst = max(worst, SEV_ORDER.index(sev))
                if sev != "none":
                    defects.append(Defect(type="whitening", location=name, severity=sev, confidence=round(z.confidence, 2)))

        if not found_any:
            quality = "insufficient"
        elif not backs:
            quality = "good"
        elif len(unassessed) >= 6:
            quality = "insufficient"
        elif unassessed or all(p.blurry or p.bg_bright for p in backs):
            quality = "limited"
        else:
            quality = "good"

        overall = "unclear" if (not backs or len(unassessed) > 4) else \
            ["near_mint_or_better", "light_play", "moderate_play", "heavy_play"][worst]
        parts = []
        for n in ZONES:
            vals = [p.zones[n].score for p in backs if n in p.zones and p.zones[n].score is not None]
            if vals:
                parts.append(f"{n}:{max(vals):.2f}")
        scores = ", ".join(parts)
        summary = (f"Back-only automated check ({len(backs)} back photo(s) of {len(images)}). "
                   + (f"Whitening scores: {scores}." if backs else "No back photo identified."))
        return ConditionReport(
            image_quality=quality, front_visible=False, back_visible=bool(backs), defects=defects,
            overall=overall, summary=summary, scope="back_only", unassessed_zones=unassessed,
            model="classical-v1")
