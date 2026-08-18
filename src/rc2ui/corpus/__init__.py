"""Discovery and isolated execution of external RC test corpora."""

from rc2ui.corpus.discovery import discover_corpus
from rc2ui.corpus.model import CorpusCase, CorpusCaseKind

__all__ = ["CorpusCase", "CorpusCaseKind", "discover_corpus"]
