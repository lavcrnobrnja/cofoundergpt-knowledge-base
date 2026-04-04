"""Prompts and templates for wiki page compilation."""

COMPILE_PROMPT = """You are maintaining a personal knowledge wiki for Lav. Compile knowledge
from multiple sources into a single, coherent wiki page.

EXISTING PAGE (if updating — preserve valuable content):
{existing_content}

TOPIC: {topic_title}

SOURCES FOR THIS TOPIC:
{sources_context}

OTHER TOPICS IN THE WIKI (for discovering connections):
{index_context}

RULES:
1. Follow the template exactly: Overview, Key Themes, Connections, Sources, Open Questions.
2. SYNTHESIZE — find threads connecting sources. Don't just list them.
3. Add [[wiki-links]] to other topics where genuine connections exist.
4. "Key Themes" = IDEAS, not source summaries.
5. "Open Questions" = genuine gaps worth exploring.
6. Keep it concise. Reference, not essay.
7. If >15 sources span unrelated subtopics, append:
   SPLIT_SUGGESTED: ["Subtopic A", "Subtopic B"]"""

WIKI_TEMPLATE = """# {title}
_Last compiled: {compiled_at} | Sources: {source_count}_

## Overview
{{overview}}

## Key Themes
{{themes}}

## Connections
{{connections}}

## Sources
| Date | Title | Key Takeaway |
|---|---|---|
{{sources_table}}

## Open Questions
{{questions}}"""
