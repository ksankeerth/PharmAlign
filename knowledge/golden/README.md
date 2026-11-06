# Golden retrieval set

`retrieval-golden.jsonl` holds 132 test questions with known right answers, written from the real documents: 74 in English and 58 in Arabic.

| Type | Items | Tests |
|---|---:|---|
| Fact, numeric, definition, procedure, clause | 59 | Finding a specific answer |
| Paraphrase | 33 | Finding the answer in different words |
| Cross-language | 16 | An English question on an Arabic source, and the reverse |
| Superseded | 4 | Answering from the law in force, never the repealed one |
| No answer | 20 | Saying nothing was found, instead of guessing |

An item names the expected documents and short key phrases, not chunks, so the set still works when chunking changes. A passage counts as found when one hit from an expected document holds every key phrase.

```sh
python3 server/scripts/golden_eval.py                        # score the active snapshot on the local server
python3 server/scripts/golden_eval.py --out new.json --compare old.json
server/.venv/bin/python -I server/scripts/golden_validate.py   # after editing the set; needs knowledge/documents/
```

The evaluation only searches, so it changes nothing on the server. Read the Arabic "refused" score with care: if Arabic search returns nothing at all, every no-answer item passes.

Each `evidence` quote is a short extract kept to prove the answer. It is not a copy of the document.
