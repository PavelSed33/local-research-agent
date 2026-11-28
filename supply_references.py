"""Dated primary-source excerpts for a qualified business supply review."""
import hashlib
import json
from pathlib import Path
import re

from agent import AgentError, Source

INDEX = Path(__file__).parent / 'fixtures' / 'supply_references.json'
INDEX_SHA256 = 'a171ebbf8b6fa75eda4d1f9576d9f978ab646e3a2faf31ded81647567e016544'


def supply_review_materials(document):
    """Return the public index and one locally selected, citable article per act."""
    try:
        raw = INDEX.read_bytes()
    except OSError as exc:
        raise AgentError('Указатель материалов по поставке недоступен.') from exc
    if hashlib.sha256(raw).hexdigest() != INDEX_SHA256:
        raise AgentError('Нарушена целостность указателя материалов по поставке.')
    index = json.loads(raw)
    cited = set(re.findall(r'(?:стат(?:ья|ьи|ье|ей|ью)|ст\.)\s*(\d+(?:\.\d+)?)', document.casefold()))
    terms = set(re.findall(r'[а-яё]{4,}', document.casefold()))
    # Generic contract vocabulary adds little evidence for choosing a particular article.
    terms -= {'договор', 'договора', 'статья', 'статьи', 'товар', 'товара', 'товаров'}
    materials = {}
    for capture in index['sources']:
        articles = (a for a in index['articles'] if a['source_id'] == capture['id'])

        def relevance(article):
            words = set(re.findall(r'[а-яё]{4,}', article['title'].casefold()))
            return (article['number'] in cited, len(terms & words), article['number'] in {'432', '506'})

        article = max(articles, key=relevance)
        heading = f"Статья {article['number']}. {article['title']}"
        material = Source(f"{capture['title']}. {heading}", capture['url'],
                          heading + '\n' + article['excerpt'], 'supply review snapshot')
        materials[material] = {
            'source_id': capture['id'], 'article_number': article['number'],
            'response_sha256': capture['response_sha256'], 'article_sha256': article['article_sha256'],
            'retrieved_at': capture['retrieved_at'], 'capture_kind': 'official_consolidated_snapshot_excerpt',
        }
    return index, materials
