"""Compatibility imports; formal Agent code never imports this test module."""
from experiments.text_evidence_protocol import (
    MAX_OUTPUT_BYTES, OUTPUT_VERSION, OfflineTextRun, TextFactJudgment, TextJudgeOutput,
    TextReference, TextRelationJudgment, evaluate, locate_fragment, output_schema, protocol,
)
