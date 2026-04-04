"""LLM prompt constants for enrichment stages."""

SUMMARY_PROMPT = """Summarize this content for a personal knowledge base.

CONTENT:
{content}

Return JSON:
{{
    "summary": "2-4 sentence plain text overview",
    "key_insights": ["3-5 actionable insight strings"],
    "author": "author name if detectable, else null"
}}"""

EXTRACTION_PROMPT = """Extract entities and assign topics for this source.

SOURCE:
Title: {title}
Summary: {summary}
Key Insights: {key_insights}

EXISTING TOPICS IN THE KNOWLEDGE BASE:
{existing_topics}

Return JSON:
{{
    "entities": [
        {{"type": "person|company|concept", "name": "..."}}
    ],
    "topics": ["existing-slug-1", "existing-slug-2"],
    "new_topics": [{{"slug": "new-slug", "title": "New Topic Title"}}]
}}

RULES:
- Prefer assigning to existing topics. Only propose new topics if nothing fits.
- Max 4 topics per source. Max 8 entities.
- Companies: only if specifically named and discussed, not passing mentions.
- Topic slugs: lowercase, hyphen-separated."""
