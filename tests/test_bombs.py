from pathlib import Path
import tempfile
import unittest

from bot.stats import ProfileStatsStore


class BombTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'stats.json'
        self.store=ProfileStatsStore(self.path)
        self.store.claim_daily_bombs(10,1)
        self.store.claim_daily_bombs(10,1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_specific_and_excess_use_actual_inventory(self):
        self.assertEqual(self.store.spend_bomb(10,1),(1,5))
        self.assertEqual(self.store.spend_bomb(10,1,2),(2,3))
        spent,left=self.store.spend_bomb(10,1,999)
        self.assertEqual((spent,left),(3,0))
        self.store.record_bombed(10,2,spent)
        self.assertEqual(ProfileStatsStore(self.path).spend_bomb(10,1,99),(0,0))
        self.assertEqual(self.store._guild_users(self.store._load(),10)['2']['bombed'],3)

    def test_invalid_amount_does_not_change_inventory(self):
        for count in (0,-1,1.5,True):
            with self.assertRaises(ValueError):
                self.store.spend_bomb(10,1,count)
        self.assertEqual(self.store.spend_bomb(10,1,100),(6,0))

    def test_inventory_stays_separate_for_each_member_and_server(self):
        self.assertEqual(self.store.spend_bomb(10,2,99),(0,0))
        self.assertEqual(self.store.spend_bomb(11,1,99),(0,0))
        self.assertEqual(self.store.spend_bomb(10,1,99),(6,0))
