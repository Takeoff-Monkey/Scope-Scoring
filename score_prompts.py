"""
Prompt templates for the ERW Job Scorer.
"""

import json
import re


def _slugify(name):
    """Convert a company name to a snake_case JSON key."""
    return re.sub(r'[^a-z0-9_]', '', name.lower().replace(' ', '_'))


def build_score_prompt(scope_data, scopes=None):
    """Build the Claude scoring prompt from scope data extracted from Scope Extractor JSON."""
    scopes_note = (
        f"\n**Scope categories tracked for this extraction run:** {json.dumps(scopes)}\n"
        if scopes else ""
    )

    return f"""You are an expert construction estimator familiar with ERW Site Solutions, a Texas-based exterior improvements contractor. Analyze this Scope Extractor output and score the job for each of their four companies.

## Scope Data Summary

**Total pages analyzed:** {scope_data['total_sheets']}
**Pages with identifiable scope:** {scope_data['sheets_with_scope']}
{scopes_note}
**Scope indicator counts (pages where each category was marked true):**
{json.dumps(scope_data['scope_indicator_counts'], indent=2)}

**Detailed page-by-page scope (pages with marked scope items or useful summaries):**
{json.dumps(scope_data['sheet_details'], indent=2)}

## Scoring Instructions

Score each company from 0-5 based on estimated scope value:
- **0**: No meaningful scope for this company
- **1**: Minimal scope, clearly under $250k, only useful to complete a package
- **2**: Light scope, borderline viability ($100-250k range)
- **3**: Decent scope, likely meets $250k threshold, worth pursuing
- **4**: Strong scope, clearly exceeds $250k, high priority
- **5**: Excellent scope, major opportunity ($500k+), top tier

## Company Scope Mapping

Use both the `scope_indicator_counts` keys and keywords in `scope_summary` text to assess each company.

**ERW Retaining Walls**: Look for scope indicators and summary keywords related to retaining walls, MSE walls, gravity walls, boulder walls, grade changes, tiered walls, structural walls, segmental block walls.

**Kaufman Concrete**: Look for scope indicators and summary keywords related to concrete flatwork, sidewalks, curb and gutter, concrete paving, driveways, ADA ramps, concrete steps, reinforced concrete slabs, concrete pavers, unit paving.

**Landtec Landscape**: Look for scope indicators and summary keywords related to softscape, landscape planting, trees, shrubs, sod, turf, mulch, irrigation systems, planting beds, groundcover, artificial turf, synthetic turf.

**Ratliff Hardscape**: Look for scope indicators and summary keywords related to pavers, unit paving, concrete pavers, stone, decomposed granite, aggregates, gravel, site furnishings, benches, water features, pools, outdoor amenities, pavilions, playground equipment.

## Important Considerations

1. **Page count matters**: More pages with scope = larger project
2. **Density ratings**: "High" density pages have more work than "Low" density
3. **Cross-reference summaries**: The scope_summary text often contains details not captured in scope flags
4. **Package value**: Even if one company has low scope, it might still be valuable to complete a turnkey package
5. **Scope categories are dynamic**: The tracked categories depend on what was selected for this extraction run — absence of a flag does not mean absence of that work; check scope_summary text carefully

Respond with ONLY a JSON object in this exact format:
{{
    "erw_retaining_walls": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "kaufman_concrete": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "landtec_landscape": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "ratliff_hardscape": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "overall_recommendation": "<1-2 sentence summary of opportunity>",
    "package_score": <0-5 overall attractiveness as turnkey package>
}}"""


def build_dynamic_score_prompt(scope_data, companies, scopes=None):
    """
    Build the Claude scoring prompt with a dynamic list of companies.

    Args:
        scope_data: dict from prepare_scope_summary_from_json / combine_scope_data
        companies:  list of dicts with keys:
                      name     (str)  — company display name
                      keywords (list) — scope keywords to look for
        scopes:     optional list of scope category strings from the Scope Extractor run
    """
    scopes_note = (
        f"\n**Scope categories tracked for this extraction run:** {json.dumps(scopes)}\n"
        if scopes else ""
    )

    # Build "Company Scope Mapping" lines dynamically
    mapping_lines = "\n\n".join(
        f"**{c['name']}**: Look for scope indicators and summary keywords related to {', '.join(c['keywords'])}."
        for c in companies
    )

    # Build the JSON response format block dynamically.
    # Constructed as a plain string so curly braces don't need escaping inside the f-string below.
    company_json_entries = ",\n".join(
        f'    "{_slugify(c["name"])}": {{\n'
        f'        "score": <0-5>,\n'
        f'        "reasoning": "<brief explanation of score>",\n'
        f'        "key_indicators": ["<specific items found>"]\n'
        f'    }}'
        for c in companies
    )
    json_format = (
        "{\n"
        + company_json_entries
        + ',\n    "overall_recommendation": "<1-2 sentence summary of opportunity>",\n'
        + '    "package_score": <0-5 overall attractiveness as turnkey package>\n}'
    )

    return f"""You are an expert construction estimator. Analyze this Scope Extractor output and score the job for each of the companies listed below.

## Scope Data Summary

**Total pages analyzed:** {scope_data['total_sheets']}
**Pages with identifiable scope:** {scope_data['sheets_with_scope']}
{scopes_note}
**Scope indicator counts (pages where each category was marked true):**
{json.dumps(scope_data['scope_indicator_counts'], indent=2)}

**Detailed page-by-page scope (pages with marked scope items or useful summaries):**
{json.dumps(scope_data['sheet_details'], indent=2)}

## Scoring Instructions

Score each company from 0-5 based on estimated scope value:
- **0**: No meaningful scope for this company
- **1**: Minimal scope, clearly under $250k, only useful to complete a package
- **2**: Light scope, borderline viability ($100-250k range)
- **3**: Decent scope, likely meets $250k threshold, worth pursuing
- **4**: Strong scope, clearly exceeds $250k, high priority
- **5**: Excellent scope, major opportunity ($500k+), top tier

## Company Scope Mapping

Use both the `scope_indicator_counts` keys and keywords in `scope_summary` text to assess each company.

{mapping_lines}

## Important Considerations

1. **Page count matters**: More pages with scope = larger project
2. **Density ratings**: "High" density pages have more work than "Low" density
3. **Cross-reference summaries**: The scope_summary text often contains details not captured in scope flags
4. **Package value**: Even if one company has low scope, it might still be valuable to complete a turnkey package
5. **Scope categories are dynamic**: The tracked categories depend on what was selected for this extraction run — absence of a flag does not mean absence of that work; check scope_summary text carefully

Respond with ONLY a JSON object in this exact format:
{json_format}"""
