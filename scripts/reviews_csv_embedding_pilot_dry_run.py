"""Offline proposal from the saved coverage record; never reads DB or encodes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'docs/evaluation/rag-all-products-coverage-20261112T071552646424Z.json'
RECORD_SHA256 = 'd8bf4967c69091a2a736acff12276109d5d1ebdbdb4137970ea5902150c80433'
PROFILE = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
SPACE = 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
PRODUCTS = ('100019386660', '100013588053')


def plan(record, product, limit):
    if product not in PRODUCTS or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('invalid_pilot_scope_or_limit')
    if (record.get('status') != 'COMPLETED' or record.get('profile_key') != PROFILE
            or record.get('embedding_space_id') != SPACE or record.get('rollback') is not True
            or record.get('closed') is not True):
        raise ValueError('frozen_coverage_identity_invalid')
    matches = [row for row in record.get('products', []) if
        (row.get('source'), row.get('dataset_version'), row.get('product_id')) == ('reviews_csv', 'reviews-csv', product)]
    if len(matches) != 1:
        raise ValueError('frozen_product_not_unique')
    row = matches[0]
    effective, usable, missing = (row.get(k) for k in ('effective_opinions', 'usable_vectors', 'missing_usable_opinions'))
    if (any(type(v) is not int or v < 0 for v in (effective, usable, missing))
            or effective != usable + missing or row.get('retrieval_status') == 'UNKNOWN'):
        raise ValueError('frozen_product_not_certified')
    return dict(status='OFFLINE_DRY_RUN_PROPOSAL', product_id=product, source='reviews_csv',
        dataset_version='reviews-csv', profile_key=PROFILE, embedding_space_id=SPACE,
        historical_scope=dict(date_from=row.get('date_from'), date_to=row.get('date_to')),
        observed_at=record.get('snapshot', {}).get('observed_at'), effective_opinions=effective,
        usable_vectors=usable, missing_usable_opinions=missing, requested_max=limit,
        upper_bound_new_encodings=min(limit, missing), selected_opinions=None,
        live_snapshot_verified=False, current_selection_verified=False, write_ready=False,
        database_connections=0, embedding_calls=0, database_writes=0,
        explanation='历史统计只能给出上限；实时只读计划、完整评论分组、来源校验和独立写入授权均未完成。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-id', required=True, choices=PRODUCTS)
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()
    try:
        raw = RECORD.read_bytes()
        if sha256(raw).hexdigest() != RECORD_SHA256:
            raise ValueError('frozen_coverage_digest_mismatch')
        print(json.dumps(plan(json.loads(raw), args.product_id, args.limit), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps(dict(status='BLOCKED', error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
