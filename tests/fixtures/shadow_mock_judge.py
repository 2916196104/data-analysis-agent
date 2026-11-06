"""Synthetic injectable provider. Never imports or calls a real model/database."""
import json
import os
from pathlib import Path
from threading import Thread
from time import sleep


class MockJudge:
    def __init__(self, mode='valid', marker=None, closed_marker=None, reply=None, secret='SYNTHETIC-SECRET'):
        self.mode, self.marker, self.closed_marker, self.reply, self.secret = mode,marker,closed_marker,reply,secret

    def judge(self, payload_json):
        if self.marker:
            Path(self.marker).write_text(str(os.getpid()),encoding='ascii')
        request = json.loads(payload_json)
        if self.mode == 'hang':
            while True:
                sleep(0.02)
        if self.mode == 'exit':
            os._exit(17)
        if self.mode == 'missing':
            os._exit(0)
        if self.mode == 'error':
            raise RuntimeError(self.secret)
        if self.mode == 'oversize':
            return '🎈'*300000
        if self.mode == 'invalid_type':
            return {'invalid':'return'}
        if self.mode == 'invalid_utf8':
            return '\ud800'
        if self.mode == 'invalid_json':
            return 'not JSON '+self.secret
        if self.mode == 'linger':
            Thread(target=lambda: sleep(60),daemon=False).start()
        if self.mode == 'noisy':
            print(self.secret)
            os.write(2,self.secret.encode())
        if self.reply is not None:
            return self.reply
        facts = []
        for fact in request['contract']['required_facts']['items']:
            for row in request['candidates']:
                span=dict(key=row['key'],source_identity_sha256=row['source_identity_sha256'],start=row['quote_start'],
                    end=row['quote_end'],text=row['quote'])
                supported = self.mode in ('support','noisy','linger')
                facts.append(dict(fact_id=fact['id'],key=row['key'],status='supported' if supported else 'unknown',
                    support_refs=[span] if supported else [],reason_code='direct_report' if supported else 'semantics_unknown',
                    rationale='Synthetic mock, not semantic validation'))
        relations=[dict(relation_id=f'relation_{i}',status='unknown',reason_code='relation_unknown',rationale='Synthetic')
                   for i,_ in enumerate(request['contract']['required_facts']['relations'])]
        value=dict(version='p5b-evidence-output-draft-v1',request_id=request['request_id'],input_sha256=request['input_sha256'],
                   execution_status='completed',fact_judgments=facts,relation_judgments=relations)
        if self.mode=='unknown_fact':
            value['fact_judgments'][0]['fact_id']='made_up'
        if self.mode=='out_of_quote':
            value['fact_judgments'][0]['support_refs'][0]['end']+=1
        if self.mode=='incomplete':
            value['fact_judgments'].pop()
        return json.dumps(value,ensure_ascii=False)

    def close(self):
        if self.closed_marker:
            Path(self.closed_marker).write_text('closed',encoding='ascii')
