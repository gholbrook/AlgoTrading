import unittest
from research.value_area_bullish import daily_ema, regimes


class RegimeTests(unittest.TestCase):
    def test_complete_daily_closes_and_seed(self):
        bars=[{'ts':day*86400+i*300,'close':100+day} for day in range(5) for i in range(288)]
        ema=daily_ema(bars,period=3)
        self.assertNotIn(86400,ema)
        self.assertEqual(ema[2*86400],101)
        self.assertEqual(ema[3*86400],102)
        self.assertEqual(ema[4*86400],103)
        incomplete=daily_ema(bars[:-1],period=3)
        self.assertNotIn(4*86400,incomplete)

    def test_entry_regime_ignores_current_and_future_daily_closes(self):
        ema={day*86400:100+day for day in range(10)}
        bar={'ts':8*86400+300,'close':108}
        before=regimes(bar,ema)
        self.assertTrue(before['rising_and_above'])
        self.assertEqual(before['ema'],107)
        ema[8*86400]=1
        ema[9*86400]=100000
        self.assertEqual(regimes(bar,ema),before)
        self.assertIsNone(regimes({'ts':86400,'close':100},ema))
