"""Copy the existing synthetic workbench corpus into the supported MVP source."""
from contextlib import closing
import csv
import json

from test_review_workbench import IDENTITY
from data_analysis_agent.reviews.loader import SOURCE, DATASET_VERSION
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl


def install_csv_copy(fixture):
    with closing(connect_review_database(fixture.db, readonly=True)) as connection:
        reviews = [dict(row) for row in connection.execute(
            'SELECT id,product_id,user_id,text,rating,published_at FROM reviews WHERE source=? AND dataset_version=? ORDER BY id',
            (SOURCE, DATASET_VERSION)).fetchall()]
        annotations = {}
        for review in reviews:
            annotation = connection.execute('SELECT id,status,error_code FROM review_annotations WHERE review_id=? '
                'AND requested_model=? AND base_url=? AND prompt_version=?',
                (review['id'], IDENTITY['model'], IDENTITY['base_url'], IDENTITY['prompt_version'])).fetchone()
            if annotation:
                opinions = [dict(row) for row in connection.execute(
                    'SELECT aspect,sentiment,quote FROM review_opinions WHERE annotation_id=? ORDER BY id',
                    (annotation['id'],)).fetchall()]
                annotations[(review['product_id'], review['user_id'])] = (annotation['status'], annotation['error_code'], opinions)
    rows = [dict(product_id=row['product_id'], product_name='SIMULATED 商品 '+row['product_id'],
                 user_id=row['user_id'], text=row['text'], rating=row['rating'], published_at=row['published_at'])
            for row in reviews]
    path = fixture.root / 'p8c5-workbench.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    import_amazon_jsonl(fixture.db, reviews_path=path, dataset_version='reviews-csv')
    with closing(connect_review_database(fixture.db, readonly=True)) as connection:
        keys = {row['id']: (row['product_id'], row['user_id']) for row in connection.execute(
            "SELECT id,product_id,user_id FROM reviews WHERE source='reviews_csv' AND dataset_version='reviews-csv'")}
    for review in pending_reviews(fixture.db, **IDENTITY, source='reviews_csv', dataset_version='reviews-csv', limit=100):
        entry = annotations.get(keys[review['id']])
        if entry is None:
            continue
        status, error_code, opinions = entry
        if status == 'success':
            save_annotation(fixture.db, review, **IDENTITY, status=status,
                opinions=validate_opinions(json.dumps({'opinions': opinions}), review['text']))
        else:
            save_annotation(fixture.db, review, **IDENTITY, status=status, error_code=error_code)
