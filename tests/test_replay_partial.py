"""Replay account snapshots retain remaining size after partial exits."""
import unittest
from unittest.mock import patch

from gridlab.storage import Store


class PartialReplayTests(unittest.TestCase):
    def test_partial_exit_keeps_layer_open_with_remaining_fee(self):
        lot = dict(lot_id=1, entry_ts=1, direction='long', entry_price=100,
                   qty=10, entry_fee=1, stop=90, target=110, unrealized_pnl=-1)
        remaining = {**lot, 'qty': 6, 'entry_fee': .6, 'target': 120}
        points = [dict(ts=ts, warmup=False) for ts in (1, 2, 3)]
        decisions = [dict(events=[dict(kind='entry', lot_id=1, entry_ts=1, fee=1)]),
                     dict(events=[dict(kind='exit', lot_id=1, entry_ts=1, qty=4)]),
                     dict(events=[dict(kind='exit', lot_id=1, entry_ts=1, qty=6)])]
        with patch('gridlab.storage.engine.new_state', return_value={}), \
             patch('gridlab.storage.engine.step', side_effect=decisions), \
             patch('gridlab.storage.engine.summary', return_value={}), \
             patch('gridlab.storage.engine.open_lots', side_effect=[[lot], [remaining], []]):
            replay = Store._replay_account(points, {})
        layer, = replay['layers']
        self.assertEqual(layer['exit_ts'], 3)
        self.assertEqual(layer['qty'], 10)
        self.assertEqual(layer['entry_fee'], 1)
        self.assertEqual(layer['changes'][0]['ts'], 2)
        self.assertEqual(layer['changes'][0]['qty'], 6)
        self.assertEqual(layer['changes'][0]['entry_fee'], .6)
