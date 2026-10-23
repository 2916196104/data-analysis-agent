"""共享观点契约不能使现有标注配置失效。"""

import json
import unittest

from data_analysis_agent.reviews import labeling as review_labeler
from data_analysis_agent.reviews import opinions


class SharedOpinionTests(unittest.TestCase):
    def test_legacy_contract_is_shared_and_prompt_identity_is_stable(self):
        for name in ("ASPECTS", "Opinion", "Extraction", "OpinionValidationError", "validate_opinions"):
            self.assertIs(getattr(review_labeler, name), getattr(opinions, name))
        self.assertEqual(review_labeler.PROMPT_VERSION,
                         "review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e")

    def test_shared_validation_keeps_exact_unicode_spans_and_rejects_fabrication(self):
        text = "🙂包装破了，包装破了。"
        item = {"aspect": "packaging", "sentiment": "negative", "quote": "包装破了"}
        result = opinions.validate_opinions(json.dumps({"opinions": [item, item]}), text)
        self.assertEqual(result, [{**item, "quote_start": 1, "quote_end": 5}])
        with self.assertRaises(opinions.OpinionValidationError):
            opinions.validate_opinions(json.dumps({"opinions": [{**item, "quote": "商品坏了"}]}), text)


if __name__ == "__main__":
    unittest.main()
