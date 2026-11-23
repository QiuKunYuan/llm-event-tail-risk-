"""Functional checks for observable timing and full snapshot replacement."""
import unittest
from point_in_time import available_session, snapshot_at, temporal_partition
from extraction_schema import strict_json_output, parse_json_output


class ProtocolTests(unittest.TestCase):
    def test_strict_output_does_not_accept_cleanup_or_extra_fields(self):
        valid = '{"direction":"改善","causes":[],"evidence":["利润增长"]}'
        self.assertIsNotNone(strict_json_output(valid))
        self.assertIsNone(strict_json_output('```json\n' + valid + '\n```'))
        self.assertIsNotNone(parse_json_output('说明：' + valid))
        self.assertIsNone(strict_json_output(valid[:-1] + ',"extra":1}'))
        self.assertIsNone(strict_json_output('{"direction":"上涨","causes":[],"evidence":[]}'))

    def test_date_only_next_session(self):
        days = ["2020-01-03", "2020-01-06", "2020-01-07"]
        self.assertEqual(available_session("2020-01-03", days), "2020-01-06")
        self.assertEqual(available_session("2020-01-04", days), "2020-01-06")
        self.assertIsNone(available_session("2020-01-07", days))

    def test_full_snapshot_removes_disappeared_holder(self):
        groups = [dict(company="A", available_date="2020-01-01", report_date="2019-09-30", version=0, complete=True, holders=[{"id": "X", "share": .1}]),
                  dict(company="A", available_date="2020-04-01", report_date="2019-12-31", version=1, complete=True, holders=[{"id": "Y", "share": .2}])]
        self.assertEqual(snapshot_at(groups, "2020-03-31")["A"][0]["id"], "X")
        self.assertEqual(snapshot_at(groups, "2020-04-01")["A"][0]["id"], "Y")
        groups[1]["complete"] = False
        with self.assertRaises(ValueError):
            snapshot_at(groups, "2020-04-01")

    def test_purge(self):
        args = ("2019-12-31", "2020-01-02", "2019-12-31", "2020-01-01", "2020-12-31", "2021-01-01", "2021-12-31")
        self.assertEqual(temporal_partition(*args), "purged")

if __name__ == "__main__":
    unittest.main()
