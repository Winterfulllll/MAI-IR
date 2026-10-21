#!/usr/bin/env python3
import sys

from config import Config
from storage import Storage

MB = 1048576


def main():
    if len(sys.argv) != 2:
        print('использование: python3 src/stats.py <config.yaml>', file=sys.stderr)
        return 1
    store = Storage.from_config(Config.load(sys.argv[1]).db)
    try:
        d = store.doc_stats()
        print(f'Документов в базе:      {d["n"]}')
        if d['n']:
            raw, txt = d['raw'] or 0, d['txt_bytes'] or 0
            print(f'  «сырых» данных:       {raw / MB:.1f} МБ')
            print(f'  выделенного текста:   {txt / MB:.2f} МБ ({100 * txt / raw:.1f} %), '
                  f'{d["txt"]} символов')
            print(f'  переобкачек:          {d["recrawled"]}')
            print('\nПо источникам:')
            for s in store.source_stats():
                print(f'  {s["source"]:12s} {s["n"]:6d} док.  '
                      f'{(s["raw"] or 0) / MB:7.1f} МБ  '
                      f'{(s["txt"] or 0) // s["n"]:7d} симв./док.  {s["encodings"]}')
        print(f'\nОчередь: {store.queue_stats()}')
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
