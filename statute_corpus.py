"""Local statute corpus for legal research agent."""
import json
from pathlib import Path

class StatuteCorpus:
    """Local corpus of statute texts."""
    
    def __init__(self, base_dir='fixtures/statutes'):
        self.base_dir = Path(base_dir)
        self.metadata = self._load_metadata()
        self.cache = {}
    
    def _load_metadata(self):
        metadata_path = self.base_dir / 'metadata.json'
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {'statutes': {}}
    
    def get_statute(self, statute_id):
        if statute_id in self.cache:
            return self.cache[statute_id]
        statute_path = self.base_dir / statute_id / 'content.json'
        if not statute_path.exists():
            return None
        with open(statute_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.cache[statute_id] = data
        return data
    
    def get_article(self, statute_id, article_number):
        statute = self.get_statute(statute_id)
        if not statute:
            return None
        return statute.get('articles', {}).get(f'article_{article_number}')
    
    def search(self, query, statute_ids=None, limit=10):
        results = []
        if not statute_ids:
            statute_ids = list(self.metadata.get('statutes', {}).keys())
        for statute_id in statute_ids:
            statute = self.get_statute(statute_id)
            if not statute:
                continue
            for article_key, article in statute.get('articles', {}).items():
                if query.lower() in article.get('text', '').lower():
                    results.append({
                        'statute_id': statute_id,
                        'article': article,
                        'relevance': article.get('text', '').lower().count(query.lower())
                    })
        results.sort(key=lambda x: x['relevance'], reverse=True)
        return results[:limit]
    
    def list_statutes(self):
        return list(self.metadata.get('statutes', {}).keys())

_corpus = None

def get_corpus():
    global _corpus
    if _corpus is None:
        _corpus = StatuteCorpus()
    return _corpus

def get_statute_text(statute_id, article_number=None):
    corpus = get_corpus()
    if article_number:
        article = corpus.get_article(statute_id, article_number)
        return article['text'] if article else None
    statute = corpus.get_statute(statute_id)
    return statute.get('full_text') if statute else None

def search_statutes(query, statute_ids=None, limit=10):
    corpus = get_corpus()
    return corpus.search(query, statute_ids, limit)
