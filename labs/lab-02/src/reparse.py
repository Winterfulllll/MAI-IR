#!/usr/bin/env python3
import sys

from config import Config
from parsing import content_hash, decode, extract_text, extract_title
from storage import Storage


def reparse_document(doc, source):
    html, _ = decode(bytes(doc['raw_html']), f"charset={doc.get('encoding') or ''}")
    text = extract_text(html, source)
    fields = {'text': text, 'title': extract_title(html), 'content_hash': content_hash(text),
              'text_size': len(text), 'text_bytes': len(text.encode('utf-8'))}
    return {k: v for k, v in fields.items() if doc.get(k) != v}


def reparse(cfg, store, batch=200):
    checked = changed = 0
    delta = 0
    fields = {'raw_html': 1, 'encoding': 1, 'source': 1, 'text': 1, 'title': 1,
              'content_hash': 1, 'text_size': 1, 'text_bytes': 1}
    for doc in store.documents.find({}, fields, batch_size=batch):
        checked += 1
        update = reparse_document(doc, cfg.source(doc['source']))
        if update:
            changed += 1
            delta += update.get('text_size', doc['text_size']) - doc['text_size']
            store.documents.update_one({'_id': doc['_id']}, {'$set': update})
        if checked % 10000 == 0:
            print(f'[i] обработано {checked}, изменено {changed}')
    return checked, changed, delta


def main():
    if len(sys.argv) != 2:
        print('использование: python3 src/reparse.py <config.yaml>', file=sys.stderr)
        return 1
    cfg = Config.load(sys.argv[1])
    store = Storage.from_config(cfg.db)
    try:
        checked, changed, delta = reparse(cfg, store)
    finally:
        store.close()
    print(f'Переразобрано документов: {checked}, изменилось: {changed}, '
          f'текст вырос на {delta} символов')
    return 0


if __name__ == '__main__':
    sys.exit(main())
