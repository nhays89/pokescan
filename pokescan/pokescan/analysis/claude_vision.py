from __future__ import annotations
import base64, json, mimetypes
from pathlib import Path
from anthropic import AsyncAnthropic
from pokescan.domain.models import Listing
from .base import ConditionReport

SYSTEM = """You are an expert condition assessor for vintage Pokemon TCG cards, reviewing eBay listing photos of a RAW (ungraded) card.
Be conservative and honest: report only what you can actually see, and lower your confidence when you are unsure.

Look closely for:
- whitening / silvering on edges and corners (most visible on the dark blue border of the BACK)
- scratches and print lines on holo or glossy surfaces (often visible only at an angle or in glare)
- creases, dents, indentations, bends
- stains, water damage, residue
- edge chipping, corner wear, centering

Photo problems matter: blur, glare, low resolution, heavy cropping, sleeves/toploaders and dark backgrounds can hide or mimic defects.
image_quality: "good" = sharp, whole card, enough to judge; "limited" = usable but some blur/glare/cropping; "insufficient" = cannot reliably judge.
Severity: "minor" = only visible on close inspection; "moderate" = obvious at normal viewing; "major" = prominent/ severe.
If the photos show a PSA/BGS/CGC slab, set is_graded_slab true. If the card looks like a proxy, custom, reprint or fake, set looks_authentic false.

Respond with ONE JSON object and nothing else:
{
  "card_name": string,
  "is_graded_slab": bool,
  "looks_authentic": bool,
  "image_quality": "good"|"limited"|"insufficient",
  "front_visible": bool,
  "back_visible": bool,
  "defects": [{"type": "whitening"|"edge_wear"|"corner_wear"|"scratch"|"crease"|"dent"|"stain"|"print_line"|"other",
               "location": string, "severity": "none"|"minor"|"moderate"|"major", "confidence": number 0-1}],
  "centering": "good"|"off"|"unknown",
  "overall": "near_mint_or_better"|"light_play"|"moderate_play"|"heavy_play"|"unclear",
  "summary": string (one or two sentences)
}"""


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"No JSON object in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


class ClaudeVisionAnalyzer:
    def __init__(self, model: str = "claude-sonnet-5-5", client: AsyncAnthropic | None = None,
                 max_tokens: int = 1500):
        self.model, self.max_tokens = model, max_tokens
        self.client = client or AsyncAnthropic()  # reads ANTHROPIC_API_KEY

    async def analyze(self, listing: Listing, images: list[Path]) -> ConditionReport:
        content: list[dict] = []
        for i, path in enumerate(images, 1):
            media = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            data = base64.standard_b64encode(path.read_bytes()).decode()
            content.append({"type": "text", "text": f"Photo {i} of {len(images)}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        content.append({"type": "text", "text": f"Listing title: {listing.title}\nAssess this card. JSON only."})

        resp = await self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        report = ConditionReport.model_validate(_extract_json(text))
        report.model = self.model
        report.input_tokens = resp.usage.input_tokens
        report.output_tokens = resp.usage.output_tokens
        return report
