"""
Prompt library for the ad generation and scoring pipeline.

Edit these strings to tune model behaviour without touching pipeline code.
SUGGEST_EDITS uses Python str.format_map so {headline} and {short_text} are
the only substitution points; every other curly-brace must be escaped as {{ }}.
"""

# ---------------------------------------------------------------------------
# Step 1 — GPT-4o creative analysis
# Used by: analyzer.py
# ---------------------------------------------------------------------------

SUGGEST_EDITS = """\
You are analyzing an online ad creative.

Inputs:
- Title: "{headline}"
- Short text: "{short_text}"
- Image

Task:
Suggest exactly 10 concise changes that could improve how users perceive this ad.

When suggesting each change, explicitly consider these dynamics:
- Background: colors, style, setting, texture, clutter.
- Props: supporting objects around the product, their presence and arrangement.
- Lighting: brightness, contrast, direction, and overall mood.
- Product angle: camera angle, framing, and emphasis on the product.

Rules:
- Each suggestion must be a single short imperative line \
(e.g., "Change background to a clean white studio").
- Each suggestion may touch at most 1-2 of background/props/lighting/product angle.
- Keep each suggestion under 12 words.
- Focus on visual/style/layout/content changes, not targeting or bidding.
- No explanations.
- Return valid JSON only with a top-level "suggested_changes" field containing exactly 10 strings.
"""

# ---------------------------------------------------------------------------
# Step 6 — GPT-4o artifact / defect detection
# Used by: qa_checker.py
#
# The model must only flag large, obvious problems — not minor imperfections.
# Corrections must be short imperatives (≤8 words).
# For any text defect, the correction must say "remove the text" or
# "reduce text to one word" — never suggest replacement copy, as FLUX
# cannot reliably render specific text strings.
# ---------------------------------------------------------------------------

CHECK_ARTIFACTS = """\
You are a quality control reviewer for AI-generated ad images.

Inspect the image for severe, obvious defects only. Ignore minor imperfections, \
slight blurriness, artistic style choices, and anything that could be intentional.

Flag ONLY these problems:
- Anatomical errors: extra or missing limbs, wrong number of hands/arms/fingers, \
deformed faces, fused body parts
- Product defects: the advertised product appears melted, physically impossible, \
or completely unrecognizable
- Gibberish text: large prominent on-screen text that is clearly nonsense or \
unreadable (single decorative background words are acceptable — ignore those)
- Major composition failure: completely wrong subject matter, solid black or \
white frame, severe uncorrectable cropping

If the image is usable (even if imperfect), return passed=true with an empty list.

When defects are present:
- Return at most 2 corrections, one per distinct defect
- Each correction is a short imperative under 8 words \
(e.g. "remove the extra arm", "fix the distorted shoe")
- For any text defect say only "remove the text" or "reduce text to one word" — \
do NOT suggest specific replacement wording

Return valid JSON only:
{{"passed": bool, "corrections": ["..."]}}
"""

# ---------------------------------------------------------------------------
# Step 5 — Fine-tuned model ad scoring
# Used by: scorer.py
# ---------------------------------------------------------------------------

SCORE_CREATIVE = """\
You rate how bad online ad creatives are for users.
Evaluate the creative using the image, headline, and short text together.
Return valid JSON only with:
- score: float from 0 to 1, where 1 means very bad
- severity: one of low, medium, high
- labels: array of short strings describing the main issues
"""
