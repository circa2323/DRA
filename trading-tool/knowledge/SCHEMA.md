# Principle library format

Every book is one JSON file in this folder. The tool loads all of them at
start-up; nothing else is needed to make a new book's principles searchable.

```json
{
  "book": {"id": "OTS", "title": "101 Option Trading Secrets",
           "author": "Kenneth R. Trester", "year": 2004},
  "principles": [
    {
      "id": "OTS-024",
      "chapter": "Avoid an Option's Last Month",
      "page": 73,
      "category": "option_buying",
      "applies_to": ["long_call", "long_put"],
      "principle": "One-sentence faithful paraphrase.",
      "quote": "Short verbatim supporting quote.",
      "rule": {"type": "threshold", "metric": "days_to_expiration",
               "operator": "<", "value": 30, "action": "warn",
               "notes": "How the book states the condition."},
      "machine_checkable": true
    }
  ]
}
```

Rules:

* `id` must be unique across all books. Prefix it with the book id.
* Only record numbers the book actually states. If the book is vague, set
  `value` to `null` and explain in `notes`.
* `page` is the printed page number, so you can open the book and check.
* Extra keys (`secret`, `section`, `chapter_number`, `subsection`, ...) are
  kept in the file for reference and ignored by the loader.

## Adding a book

1. Extract the text: `pdftotext -layout book.pdf book.txt`.
2. Write `knowledge/<ID>.json` in the format above (an assistant can read the
   text and draft it; check the quotes and page numbers against the book).
3. Run `python -m optiontool principles --book <ID>` to confirm it loads.
4. A principle only *drives decisions* once a check in
   `optiontool/rules.py` cites its id. Principles without a check are still
   searchable and appear in `explain` output for their category.
