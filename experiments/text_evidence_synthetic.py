"""Fixed fictional sources through real P0 checks; no database or real encoder."""
from contextlib import contextmanager
from types import SimpleNamespace

from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_structured_evidence import prepare_input
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_store import SPACE, SPACE_ID, OpinionMatch
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SemanticQuery, SemanticRetrievalService, SnapshotInfo

SCOPE = dict(model='SYNTHETIC',base_url='https://offline.invalid/v1',prompt_version='TEXT-EXPERIMENT-SYNTHETIC-v1',
    source='SYNTHETIC',dataset_version='text-evidence-synthetic-v1',product_ids=['SYNTHETIC-TEXT-P1'],
    date_from='2032-01-01',date_to='2032-01-02')


class SyntheticEncoder:
    space = SPACE
    def embed_texts(self, texts): return [[1.0]+[0.0]*1023 for _ in texts]


class SyntheticRepository:
    def __init__(self, sources): self.sources = tuple(sources)

    @contextmanager
    def snapshot(self, scope):
        yield SimpleNamespace(scope=scope, embedding_space_id=SPACE_ID, text_builder_version='quote-utf8-v1',
            info=SnapshotInfo('TEXT-SYNTHETIC-SNAPSHOT','2032-01-03T00:00:00+00:00'),
            search=lambda vector, **kw: tuple(OpinionMatch(d,0.1) for s in self.sources for d in s.opinions)[:kw['top_k']],
            load_sources=lambda keys: self.sources)


def prepare_cases(cases):
    # Drop gold BEFORE preparing independent synthetic source records.
    inputs = [{name:c[name] for name in ('id','question','quote','review_text')} for c in cases]
    if not inputs or any(c['question'] != inputs[0]['question'] for c in inputs):
        raise ValueError('fixed_question_required')
    contract = build_query_plan(inputs[0]['question'],dict(SCOPE)).task_contract
    identity = tuple(SCOPE[k] for k in ('model','base_url','prompt_version'))
    profile = content_sha256(list(identity))
    sources = []
    for index,case in enumerate(inputs,1):
        text,quote,rid = case['review_text'],case['quote'],int(case['id'][1:])
        start = text.index(quote)
        row = dict(source=SCOPE['source'],dataset_version=SCOPE['dataset_version'],product_id=SCOPE['product_ids'][0],
            review_id=rid,annotation_id=1000+rid,human_revision_id=None,processing_status='success',human_status='unreviewed',
            error_code=None,opinion_source='model',published_at=SCOPE['date_from']+'T00:00:00+00:00',review_text=text,
            review_content_sha256=content_sha256(['TEXT-SYNTHETIC',rid,text]),import_run_id=1,line_number=rid,
            file_sha256=content_sha256('TEXT-SYNTHETIC-FILE'),model_opinion_id=index,human_opinion_id=None,
            quote=quote,quote_start=start,quote_end=start+len(quote),aspect='service',sentiment='negative')
        document = _document(row,identity,profile)
        sources.append(ReviewSource(_review_state(row,[document],profile),(document,),text))
    service = SemanticRetrievalService(encoder=SyntheticEncoder(),repository=SyntheticRepository(sources),scope=dict(SCOPE))
    result = service.search(SemanticQuery(question=inputs[0]['question']))
    request = prepare_input(contract,result,trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
                            request_id='TEXT-EVIDENCE-'+'-'.join(c['id'] for c in inputs))
    return contract,result,request
