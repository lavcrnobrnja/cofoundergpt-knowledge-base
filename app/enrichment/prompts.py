"""LLM prompt constants for enrichment stages."""

SUMMARY_PROMPT = """Summarize this content for a personal knowledge base.

CONTENT:
{content}

Return JSON:
{{
    "summary": "2-4 sentence plain text overview",
    "key_insights": ["3-5 actionable insight strings"],
    "author": "author name if detectable (e.g. host or writer), else null",
    "guests": ["names of interviewees or guests — ONLY for interviews, podcasts, and panel discussions. Leave empty for articles, tweets, essays, and other non-interview content."]
}}"""

EXTRACTION_PROMPT = """Extract entities and assign topics for this source.

SOURCE:
Title: {title}
Summary: {summary}
Key Insights: {key_insights}

CONTENT SAMPLE:
{content_sample}

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
- Extract ALL named people mentioned (interviewees, hosts, referenced figures). Do not omit anyone.
- Extract companies that are specifically discussed (not just passing mentions).
- Extract key concepts that are central to the content.
- Prefer assigning to existing topics. Only propose new topics if nothing fits.
- Max 4 topics per source. Max 20 entities.
- Topic slugs: lowercase, hyphen-separated."""
