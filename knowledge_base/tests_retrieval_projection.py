"""Projection must preserve full candidate ranking, metadata and eligibility."""

from collections import Counter
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .models import Document, KnowledgeChunk
from .retrieval import LexicalRetriever, _tokenize, _term_coverage, _has_sufficient_distinct_matches


class RetrievalProjectionTests(TestCase):
    def test_many_candidates_ties_freshness_and_metadata_match_reference(self):
        now = timezone.now()
        eligible = []
        for index, (status, source, expiry) in enumerate([
            ("INDEXED", "MANUAL", None),
            ("INDEXED", "MANUAL", None),
            ("INDEXED", "ONLINE", now + timedelta(hours=1)),
            ("INDEXED", "ONLINE", now - timedelta(hours=1)),
            ("PENDING", "MANUAL", None),
        ]):
            document = Document.objects.create(
                filename=f"source-{index}.md", source_identifier=f"test:{index}",
                status=status, source_type=source, expires_at=expiry,
            )
            KnowledgeChunk.objects.bulk_create([
                KnowledgeChunk(document=document, chunk_index=n,
                               content="alpha beta alpha" if n % 2 else "alpha gamma")
                for n in range(210)
            ])
            if index < 3:
                eligible.append(document.pk)
        counts = Counter(_tokenize("alpha beta alpha"))
        reference = []
        for chunk in KnowledgeChunk.objects.filter(document_id__in=eligible).select_related("document"):
            tokens = Counter(_tokenize(chunk.content))
            score = _term_coverage(counts, tokens)
            if _has_sufficient_distinct_matches(counts, tokens) and score >= 0.5:
                reference.append((round(score, 4), chunk.document_id, chunk.chunk_index,
                                  chunk.id, chunk.document.source_identifier,
                                  chunk.document.filename, chunk.content))
        reference.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))
        with CaptureQueriesContext(connection) as queries:
            results = LexicalRetriever().retrieve("alpha beta alpha", top_k=220, min_relevance=0.5)
        self.assertEqual([(r.score, r.document_id, r.chunk_index, r.chunk_id,
                           r.source_identifier, r.title, r.content) for r in results], reference[:220])
        self.assertEqual(len(queries), 1)  # No deferred-field N+1 reads.
        self.assertNotIn('"uploaded_at"', queries[0]["sql"])
        self.assertNotIn('"sync_query"', queries[0]["sql"])
