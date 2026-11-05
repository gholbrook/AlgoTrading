"""Execution and accounting checks for attached target ladders."""
import json
import unittest

from gridlab.file_engine import step_file
from tests.test_file_engine import bar, source, state_for


class TargetStepsTests(unittest.TestCase):
    def setup_lot(self, *, direction='long', order_type='market', **config):
        order = {'type': order_type, 'direction': direction, 'qty': 7,
                 'target_steps_pct': [1, 2, 3, 4], 'stop_pct': 5}
        if order_type == 'limit':
            order['price'] = 100
        code = source("if ctx['memory'].get('sent'): return []\n"
                      "ctx['memory']['sent'] = True\nreturn " + repr([order]))
        state = state_for(code, capital=10000, **config)
        step_file(state, bar(1))
        return state

    def test_single_entry_four_exits_and_restart_accounting(self):
        state = self.setup_lot(fee_bps=10)
        entered = step_file(state, bar(2))
        self.assertEqual(len(entered['events']), 1)
        self.assertEqual(len(state['positions']), 1)
        first = step_file(state, bar(3, high=101.5))
        self.assertEqual([e['label'] for e in first['events']], ['TP1'])
        self.assertEqual(state['positions'][0]['qty'], 5.25)
        self.assertAlmostEqual(state['positions'][0]['entry_fee'], .525)
        state = json.loads(json.dumps(state))
        rest = step_file(state, bar(4, open_=105, high=105, low=105, close=105))
        self.assertEqual([e['label'] for e in rest['events']], ['TP2', 'TP3', 'TP4'])
        self.assertTrue(all(e['price'] == 105 for e in rest['events']))
        self.assertEqual(state['positions'], [])
        self.assertAlmostEqual(state['cash'], 10000 + state['realized_pnl'])
        self.assertEqual(len({e['event_id'] for e in first['events'] + rest['events']}), 4)

    def test_same_candle_stop_wins(self):
        state = self.setup_lot()
        point = step_file(state, bar(2, high=105, low=94))
        self.assertEqual([e['reason'] for e in point['events']], ['strategy_entry', 'stop'])
        self.assertEqual(point['events'][-1]['qty'], 7)

    def test_intrabar_limit_defers_favorable_targets(self):
        state = self.setup_lot(order_type='limit')
        point = step_file(state, bar(2, open_=102, high=105, low=99, close=102))
        self.assertEqual(len(point['events']), 1)
        point = step_file(state, bar(3, open_=104, high=104, low=104, close=104))
        self.assertEqual(len(point['events']), 4)

    def test_short_ladder_and_explicit_partial(self):
        state = self.setup_lot(direction='short')
        step_file(state, bar(2))
        state['pending'] = [{'action': 'exit', 'lot_id': 1, 'qty': 3}]
        point = step_file(state, bar(3))
        self.assertEqual(point['events'][0]['qty'], 3)
        self.assertAlmostEqual(sum(t['qty'] for t in state['positions'][0]['targets']), 4)
        point = step_file(state, bar(4, open_=95, high=95, low=95, close=95))
        self.assertEqual(len(point['events']), 4)
        self.assertAlmostEqual(sum(e['qty'] for e in point['events']), 4)
        self.assertEqual(state['positions'], [])

    def test_invalid_ladder_rejected_before_cash_mutation(self):
        for steps in ([2, 1], [], [1, 1], [1, 100], [True, 2]):
            state = self.setup_lot()
            state['pending'][0]['target_steps_pct'] = steps
            with self.assertRaises(ValueError):
                step_file(state, bar(2))
            self.assertEqual(state['cash'], 10000)
