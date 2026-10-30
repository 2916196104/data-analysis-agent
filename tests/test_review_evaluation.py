"""评测判定器离线自检；不会读取密钥或调用模型。"""

import unittest

from scripts.evaluate_review_agent import SCOPE, cases, grade


class EvaluationTests(unittest.TestCase):
    def test_status_alone_cannot_pass_wrong_tool_or_parameters(self):
        case = cases()[0]
        result = dict(status="ok", scope={**SCOPE, "as_of": None}, result_ids=["r1"],
                      validation={"status": "passed"}, results={"r1": dict(kind="trend", parameters={"period": "month"})})
        self.assertFalse(grade(case, result)["trend"])
        result["results"]["r1"]["parameters"]["period"] = "day"
        self.assertTrue(all(grade(case, result).values()))
        result["scope"] = {**result["scope"], "product_ids": ["OUTSIDE"]}
        self.assertFalse(grade(case, result)["scope"])

    def test_comparison_must_not_silently_drop_a_selected_product(self):
        case = next(row for row in cases() if row["id"] == "products")
        result = dict(status="ok", scope={**SCOPE, "as_of": None}, result_ids=["r1"],
                      validation={"status": "passed"}, results={"r1": dict(kind="comparison", parameters={"product_ids": ["DEMO-A"]})})
        self.assertFalse(grade(case, result)["all_selected_products"])
        result["results"]["r1"]["parameters"]["product_ids"] = []
        self.assertTrue(all(grade(case, result).values()))

    def test_unsupported_needs_status_and_actual_overview_reference(self):
        case = next(row for row in cases() if row["id"] == "write")
        result = dict(status="ok", scope={**SCOPE, "as_of": None}, result_ids=[], results={})
        self.assertFalse(all(grade(case, result).values()))
        result.update(status="unsupported", result_ids=["overview"], results={"overview": dict(kind="context", parameters={})})
        self.assertTrue(all(grade(case, result).values()))


if __name__ == "__main__":
    unittest.main()
