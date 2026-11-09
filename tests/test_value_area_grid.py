import unittest
from research.value_area_grid import simulate_grid


def bar(open_, high, low, close):
    return dict(open=open_,high=high,low=low,close=close)


class SpotGridTests(unittest.TestCase):
    def test_initial_seed_and_liquidation_charge_both_sides(self):
        result=simulate_grid([99,101,103],100,[bar(100,100,100,100)],bar(100,100,100,100),fee_bps=20,slip_bps=0)
        expected=.5+.5/1.002*.998
        self.assertAlmostEqual(result['return_pct'],100*(expected-1))
        self.assertEqual(result['cycles'],0)
        self.assertAlmostEqual(result['return_pct'],result['passive_pct'])

    def test_new_buy_cannot_also_sell_on_same_candle(self):
        result=simulate_grid([90,110],100,[bar(100,120,80,100)],bar(100,100,100,100),fee_bps=0,slip_bps=0)
        self.assertEqual(result['cycles'],0)
        self.assertAlmostEqual(result['return_pct'],100*(100/90-1))
        result=simulate_grid([90,110],100,[bar(100,120,80,100),bar(100,120,100,110)],bar(100,100,100,100),fee_bps=0,slip_bps=0)
        self.assertEqual(result['cycles'],1)
        self.assertAlmostEqual(result['return_pct'],100*(110/90-1))

    def test_liquidation_uses_following_open_with_slippage(self):
        result=simulate_grid([99,101,103],100,[bar(100,100,100,100)],bar(80,80,80,80),fee_bps=0,slip_bps=1)
        self.assertAlmostEqual(result['return_pct'],100*(.5+.5/(100*1.0001)*(80*.9999)-1))

    def test_fixed_allocation_is_independent_of_price_location(self):
        for anchor in (91, 100, 109):
            result=simulate_grid([90,95,100,105,110],anchor,
                                 [bar(anchor,anchor,anchor,anchor)],bar(anchor,anchor,anchor,anchor),
                                 fee_bps=20,slip_bps=0,initial_inventory_pct=25)
            self.assertAlmostEqual(result['initial_inventory_allocation_pct'],25)
            expected=.75+.25/1.002*.998
            self.assertAlmostEqual(result['return_pct'],100*(expected-1))

    def test_fixed_zero_allocation_waits_for_a_buy(self):
        result=simulate_grid([90,100,110],105,[bar(105,108,104,106)],bar(106,106,106,106),
                             initial_inventory_pct=0)
        self.assertEqual(result['initial_inventory_allocation_pct'],0)
        self.assertEqual(result['fills'],0)
        self.assertEqual(result['return_pct'],0)
