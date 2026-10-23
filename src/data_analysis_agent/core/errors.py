"""数据输入和数据库契约共用的校验错误。"""


class ReviewDataError(ValueError):
    """评论输入或数据库不符合应用契约。"""
