# Open RAGBench Retrieval Eval: live-20260428-0934

- Eval version: `2026-04-28.3`
- Created at: `2026-04-28T09:20:58.066833+00:00`
- Dataset: `vectara/open_ragbench`
- Dataset revision: `63f6b052ff83508b08e242db42263ee708815c26`
- Documents: 100 (40 positive, 60 negative)
- Queries scored: 301
- Eval tag: `open-ragbench-eval-live-20260428-0934`
- Upload statuses: completed=100

## Summary

This run retrieved the expected document in the first result for 0.970 of queries and within the top 10 for 0.997 of queries.
The top-10 ranking quality was MRR@10=0.982 and nDCG@10=0.986 across 301 scored queries.
The five-question answer check produced 5/5 pass verdicts.
There were 1 retrieval misses at top 10.

Detailed artifacts: [full retrieval results](results.json), [query review](demo_queries.md), [detailed metric tables](detailed_metrics.md).

## Key Metrics

| Metric | Value |
|---|---:|
| Recall@1 | 0.970 |
| Recall@10 | 0.997 |
| MRR@10 | 0.982 |
| nDCG@10 | 0.986 |

## Notes

Lowest category Recall@10 values: `cs.CL`=0.958.

## Metric Definitions

- Recall@k: fraction of scored queries where the expected Open RAGBench document appears anywhere in the top k search results.
- MRR@k: mean reciprocal rank of the expected document when it appears in the top k; a rank-1 hit contributes 1.0, rank 3 contributes 0.333, and a miss contributes 0.
- nDCG@k: rank-discounted gain for the expected document in the top k. This run has one relevant document per query, so nDCG is 1.0 at rank 1, 1/log2(rank+1) at lower ranks, and 0 for a miss.
- Reference coverage: lightweight answer-eval heuristic measuring how many non-stopword terms from the Open RAGBench reference answer appear in the generated answer.
- Verdict: pass when the expected document is retrieved and reference coverage is at least 0.25, review when only one of those checks passes, and fail otherwise.

## External Reference Points

These are context markers, not strict leaderboard comparisons: this run scores document-level retrieval over a topic-filtered 100-PDF subset, while published systems often score passage/chunk retrieval or answer generation on different corpora.

| System / baseline | Dataset and scope | Reported metric | Source |
|---|---|---:|---|
| This app run | Open RAGBench arXiv PDF subset, 100 docs / 301 queries | Recall@10=0.997, MRR@10=0.982, nDCG@10=0.986 | this report |
| Open RAGBench full draft dataset | arXiv PDF corpus, 1000 docs, 3045 QA pairs, 400 positive docs, 600 hard negatives | dataset reference | [Vectara Open RAG Bench](https://github.com/vectara/open-rag-bench) |
| BM25 | BEIR SciFact scientific retrieval | nDCG@10=0.665 | [BEIR paper, Table 2](https://datasets-benchmarks-proceedings.neurips.cc/paper_files/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf) |
| BM25 + cross-encoder reranker | BEIR SciFact scientific retrieval | nDCG@10=0.688 | [BEIR paper, Table 2](https://datasets-benchmarks-proceedings.neurips.cc/paper_files/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf) |
| BM25 + cross-encoder reranker | BEIR 18-dataset zero-shot suite | +11% average nDCG@10 vs BM25; better than BM25 on 16/18 datasets | [BEIR paper, Table 2 and analysis](https://datasets-benchmarks-proceedings.neurips.cc/paper_files/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf) |

## Five-Question Answer Evaluation

| Query | Verdict | Expected Doc Retrieved | Reference Coverage |
|---|---|---:|---:|
| `2fab68df-dea2-46a3-8088-ca5e18ea843c` | `pass` | true | 0.433 |
| `063671c6-8536-4e8d-aaa0-3be23edf2339` | `pass` | true | 0.588 |
| `85e0dff2-6473-43e5-b555-9b69c9c31564` | `pass` | true | 1.000 |
| `1ccbaf52-d9cb-4e5b-b3b7-40bacc5a4a1f` | `pass` | true | 0.696 |
| `08188d49-213f-459b-887a-79f819d3c4a7` | `pass` | true | 0.875 |
