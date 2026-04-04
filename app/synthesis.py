"""Query synthesis — search → context assembly → Gemini Pro → answer."""
import re

from google import genai
from google.genai import types

from app import config
from app.search import vector_search, wiki_search


SYNTHESIS_PROMPT = """You are Lav's second brain. Answer his question using the provided
context. Be direct, cite your sources, and draw connections.

QUESTION: {query}

RELEVANT WIKI PAGES:
{wiki_context}

RELEVANT SOURCE EXCERPTS:
{source_context}

RULES:
1. Answer directly. Don't hedge.
2. Cite sources inline: [Source Title](url)
3. Synthesize across sources — don't just list what each one says.
4. If sources contradict, say so explicitly.
5. If the KB doesn't have enough info, say that honestly.
6. End with "Related topics: [[x]], [[y]]" if connections exist."""


async def synthesize_answer(query: str) -> dict:
    """Run search, assemble context, call Gemini Pro, return structured response."""
    # Search
    source_results = await vector_search(query, top_k=5)
    wiki_results = await wiki_search(query, top_k=2)

    # Build context strings
    if wiki_results:
        wiki_context = ""
        for wp in wiki_results:
            wiki_context += f"\n### {wp['title']}\n{wp['content']}\n"
    else:
        wiki_context = "No wiki pages available yet."

    if source_results:
        source_context = ""
        for sr in source_results:
            source_context += f"\n**{sr['source_title']}** ({sr['source_url']})\n{sr['content'][:2000]}\n"
    else:
        source_context = "No source excerpts available."

    # If we have nothing at all, return early
    if not source_results and not wiki_results:
        return {
            "answer": "I don't have any knowledge about this topic yet. Try ingesting some sources first.",
            "sources": [],
            "wiki_pages": [],
            "related_topics": [],
        }

    # Synthesize via Gemini Pro
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = SYNTHESIS_PROMPT.format(
        query=query,
        wiki_context=wiki_context,
        source_context=source_context,
    )

    response = client.models.generate_content(
        model=config.PRO_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.3),
    )

    answer = response.text

    # Extract related topics from answer (look for [[topic]] patterns)
    related_topics = re.findall(r'\[\[([^\]]+)\]\]', answer)

    return {
        "answer": answer,
        "sources": [
            {
                "id": sr["source_id"],
                "title": sr["source_title"],
                "url": sr["source_url"],
                "relevance": round(sr["score"], 3),
            }
            for sr in source_results
        ],
        "wiki_pages": [wp["slug"] for wp in wiki_results],
        "related_topics": related_topics,
    }
