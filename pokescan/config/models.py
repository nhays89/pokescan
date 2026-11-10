from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from pokescan.domain.models import Severity

Priority = Literal["high", "normal", "low"]


class FilterConfig(BaseModel):
    price_min: float | None = None
    price_max: float | None = None
    buying_options: list[Literal["FIXED_PRICE", "AUCTION", "BEST_OFFER"]] = []
    conditions: list[Literal["NEW", "USED", "UNSPECIFIED"]] = []


class SearchConfig(BaseModel):
    name: str
    enabled: bool = True
    q: str = "pokemon"                     # base query; combined with each set name
    sets: list[str] = []                   # e.g. [undaunted, call of legends]
    set_batch_size: int = Field(3, ge=1)   # sets OR-ed into one API call (fewer calls)
    exclude_terms: list[str] = []
    filters: FilterConfig = FilterConfig()
    priority: Priority = "normal"
    category_ids: list[str] | None = None  # overrides global when set
    languages: list[str] | None = None     # e.g. [English]; overrides global. [] = no language filter
    years: list[int] = []                  # e.g. [1999, 2000]; can be combined with sets
    lookback_days: int | None = None       # overrides global; 0 = off
    notify_existing: bool = False          # lookback off only: first run reports everything currently listed (else seeds silently)
    aspects: dict[str, list[str]] | None = None   # extra item-specific filters; merged over global (None = inherit)
    year_match: Literal["aspect", "title"] | None = None   # overrides global


class GlobalConfig(BaseModel):
    daily_call_budget: int = 4000
    marketplace: str = "EBAY_US"
    category_ids: list[str] = []
    languages: list[str] = []              # eBay item-specific 'Language' filter, e.g. [English]
    aspects: dict[str, list[str]] = {}     # any other item-specific filter, e.g. { Game: ["<value from pokescan.check>"] }
    page_size: int = Field(100, ge=1, le=200)
    lookback_days: int = Field(10, ge=0)            # only consider listings created in the last N days (0 = off -> silent seeding instead)
    backfill_max_pages: int = Field(10, ge=1, le=50)  # pages walked on a search's first run; stops at the lookback cutoff
    max_pages: int = Field(3, ge=1, le=10)          # pages per query; paging stops early at the first already-seen listing
    year_match: Literal["aspect", "title"] = "aspect"  # aspect = eBay item-specific filter (drops blanks); title = year as a keyword
    year_aspect_name: str = "Year Manufactured"     # verify with `python -m pokescan.check`
    intervals_minutes: dict[Priority, float] = {"high": 5, "normal": 10, "low": 30}


DEFAULT_LIMITS: dict[str, Severity] = {
    "whitening": "minor", "edge_wear": "minor", "corner_wear": "minor", "scratch": "minor",
    "crease": "none", "dent": "none", "stain": "none", "print_line": "moderate", "other": "moderate",
}


class PolicyConfig(BaseModel):
    """Max tolerated severity per defect type. Anything worse FAILs (if the model is confident)."""
    max_severity: dict[str, Severity] = {}      # partial overrides of DEFAULT_LIMITS
    min_confidence: float = Field(0.6, ge=0, le=1)  # below this, a bad defect -> REVIEW not FAIL
    require_back_photo: bool = True             # whitening shows best on the back border
    skip_graded: bool = True                    # slabs hide edges; treat as FAIL
    notify_review: bool = True                  # also ping for unclear photos / low confidence
    max_unassessed_zones: int = 2               # of 8 edge/corner zones; more hidden by glare -> REVIEW

    def limit(self, defect_type: str) -> Severity:
        return {**DEFAULT_LIMITS, **self.max_severity}.get(defect_type, "moderate")


class ClassicalConfig(BaseModel):
    """Tunables for the OpenCV back-whitening analyzer. Calibrate on real listings using debug_dir."""
    whitening_thresholds: tuple[float, float, float] = (0.03, 0.12, 0.30)  # fraction of whitish px: minor/moderate/major
    band_pct: float = 0.03          # edge band depth as fraction of card width
    inset_px: int = 3               # ignore the outermost px (contour imprecision / background leak)
    white_s_max: int = 80           # whitening = low saturation ...
    white_v_min: int = 150          # ... and bright (also adaptive vs. the card's own blue)
    glare_v: int = 245              # glare core = blown-out pixels
    glare_s: int = 40
    glare_min_px: int = 15          # glare blobs are thicker than this; whitening is a thin line
    glare_min_area: int = 500
    glare_interior_px: int = 40     # real glare reaches into the card; blobs hugging the edge are treated as wear
    glare_halo_px: int = 20         # grow the glare mask to cover its soft falloff
    glare_zone_max: float = 0.35    # zone more than this covered by glare -> "not checked"
    blur_min: float = 15.0          # variance of Laplacian below this -> low confidence
    back_blue_min: float = 0.55     # share of border-ring pixels that must be blue to call a photo the BACK
    debug_dir: str | None = None    # e.g. data/debug -> saves annotated images


class AnalysisConfig(BaseModel):
    enabled: bool = False
    analyzer: Literal["claude", "classical"] = "claude"
    model: str = "claude-sonnet-5-5"            # claude analyzer only
    max_images: int = Field(4, ge=1, le=8)
    fetch_all_photos: bool = True               # 1 getItem call per analyzed listing
    max_analyses_per_day: int = 150             # hard spend cap
    skip_title_terms: list[str] = []            # whole-word, case-insensitive; listing is ignored entirely
    allowed_languages: list[str] = []           # if a listing's Language item-specific is set and not one of these -> auto-FAIL. Blank passes.
    auto_fail_title_terms: list[str] = ["japanese", "japan", "jpn", "jp", "日本語", "日本"]  # recorded as FAIL, no photos/API spent
    classical: ClassicalConfig = ClassicalConfig()
    policy: PolicyConfig = PolicyConfig()


BuyOpt = Literal["FIXED_PRICE", "AUCTION", "BEST_OFFER"]


class WatchDefaults(BaseModel):
    """Applied to every watchlist card unless the card overrides it."""
    priority: Priority = "normal"
    exclude_terms: list[str] = []
    buying_options: list[BuyOpt] = []
    notify_existing: bool = False   # True: first run also reports every listing that exists right now
    analyze: bool = False           # True: run photo analysis and put the verdict in the push (never silent)
    lookback_days: int = Field(0, ge=0)   # 0 = no date window: any NEW listing notifies, whatever its age


class WatchCard(BaseModel):
    name: str
    query: str | None = None        # eBay keywords; defaults to name. Quote phrases: '"dark golduck"'
    enabled: bool = True
    min_price: float | None = None
    max_price: float | None = None
    buying_options: list[BuyOpt] | None = None
    priority: Priority | None = None
    exclude_terms: list[str] | None = None
    notify_existing: bool | None = None
    analyze: bool | None = None
    lookback_days: int | None = Field(None, ge=0)


class WatchlistConfig(BaseModel):
    defaults: WatchDefaults = WatchDefaults()
    cards: list[WatchCard] = []


class ResolvedWatch(BaseModel):
    """A watch card with defaults filled in."""
    name: str
    query: str
    enabled: bool
    min_price: float | None
    max_price: float | None
    buying_options: list[BuyOpt]
    priority: Priority
    exclude_terms: list[str]
    notify_existing: bool
    analyze: bool
    lookback_days: int

    def to_search(self) -> SearchConfig:
        return SearchConfig(
            name=f"watch:{self.name}", q=self.query, priority=self.priority, exclude_terms=self.exclude_terms,
            filters=FilterConfig(price_min=self.min_price, price_max=self.max_price, buying_options=self.buying_options),
            lookback_days=self.lookback_days, notify_existing=self.notify_existing)


class AppConfig(BaseModel):
    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    searches: list[SearchConfig]
    analysis: AnalysisConfig = AnalysisConfig()
    watchlist: WatchlistConfig = WatchlistConfig()

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def unique_names(self):
        names = [s.name for s in self.searches]
        if len(names) != len(set(names)):
            raise ValueError("search names must be unique")
        wn = [c.name for c in self.watchlist.cards]
        if len(wn) != len(set(wn)):
            raise ValueError("watchlist card names must be unique")
        return self

    def watch_cards(self) -> list[ResolvedWatch]:
        d, out = self.watchlist.defaults, []
        for c in self.watchlist.cards:
            pick = lambda v, dv: dv if v is None else v
            out.append(ResolvedWatch(
                name=c.name, query=c.query or c.name, enabled=c.enabled, min_price=c.min_price, max_price=c.max_price,
                buying_options=pick(c.buying_options, d.buying_options), priority=pick(c.priority, d.priority),
                exclude_terms=pick(c.exclude_terms, d.exclude_terms), notify_existing=pick(c.notify_existing, d.notify_existing),
                analyze=pick(c.analyze, d.analyze), lookback_days=pick(c.lookback_days, d.lookback_days)))
        return out

    def all_searches(self) -> list[SearchConfig]:
        """Year/set searches plus watchlist cards (as searches named 'watch:<name>')."""
        return [*self.searches, *[w.to_search() for w in self.watch_cards() if w.enabled]]
