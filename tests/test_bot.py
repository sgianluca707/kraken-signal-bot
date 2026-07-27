import json
import tempfile
import unittest
from pathlib import Path

import bot


class BotTests(unittest.TestCase):
    def test_ema(self):
        values = [1, 2, 3, 4, 5]
        result = bot.ema(values, 3)
        self.assertEqual(len(result), len(values))
        self.assertGreater(result[-1], result[0])

    def test_net_rr_positive(self):
        rr = bot.net_rr(100, 98, 105, 0.001)
        self.assertGreater(rr, 1)

    def test_fmt_price_italian(self):
        self.assertEqual(bot.fmt_price(1234.5), "1.234,50")
        self.assertEqual(bot.fmt_price(12.5), "12,5000")

    def test_resolve_pairs(self):
        cfg = {"assets": [{"asset": "BTC", "pair_candidates": ["BTC/EUR", "XBT/EUR"]}]}
        pairs = {"XXBTZEUR": {"wsname": "XBT/EUR", "altname": "XBTEUR", "status": "online"}}
        resolved = bot.resolve_pairs(cfg, pairs)
        self.assertIn("BTC", resolved)
        self.assertEqual(resolved["BTC"]["api_pair"], "XXBTZEUR")

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            bot.save_json_atomic(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})


if __name__ == "__main__":
    unittest.main()
