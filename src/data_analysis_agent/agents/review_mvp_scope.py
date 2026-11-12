"""MVP policy for new assistant jobs; historical reads and non-Agent jobs stay unchanged."""
from contextlib import closing
import re

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.store import connect_review_database

AGENT_SOURCE = 'reviews_csv'
AGENT_DATASET_VERSION = 'reviews-csv'


def validate_agent_scope(parameters, business_db=None):
    if (parameters.get('source'), parameters.get('dataset_version')) != (AGENT_SOURCE, AGENT_DATASET_VERSION):
        raise ReviewDataError('AI 助手 MVP 仅支持 reviews_csv / reviews-csv；其他来源的历史任务仍可查看，请切换数据集后重新提问。')
    if re.search(r'amazon_reviews_2023|amazon-reviews-2023|(?:Amazon|亚马逊)\s*(?:的|上(?:的)?|来源的)?\s*(?:商品|产品|数据集|数据源)',
                 parameters.get('question') or '', re.I):
        raise ReviewDataError('本轮明确指定了 Amazon 商品或数据源；AI 助手 MVP 仅支持 reviews_csv / reviews-csv，不支持跨来源分析。')
    selection = parameters.get('selection_scope') or parameters
    if (selection.get('source'), selection.get('dataset_version')) != (AGENT_SOURCE, AGENT_DATASET_VERSION):
        raise ReviewDataError('页面选择与 AI 助手执行数据集不一致，请重新确认范围。')
    if business_db is None:
        return
    with closing(connect_review_database(business_db, readonly=True)) as connection:
        catalog = {row['product_id'] for row in connection.execute(
            'SELECT product_id FROM products WHERE source=? AND dataset_version=?',
            (AGENT_SOURCE, AGENT_DATASET_VERSION)).fetchall()}
    if not catalog:
        raise ReviewDataError('AI 助手数据集 reviews_csv / reviews-csv 当前不可用，请先确认数据集；不会自动切换其他来源。')
    requested = set(parameters.get('product_ids', [])) | set(selection.get('product_ids', []))
    if requested - catalog:
        raise ReviewDataError('部分商品不属于 reviews_csv / reviews-csv，请重新选择当前数据集中的商品。')
