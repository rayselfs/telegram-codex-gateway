from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import schedules

class TimezoneTests(unittest.TestCase):
    def test_configured_timezone_and_dst_do_not_duplicate_or_invent_wall_time(self):
        zone=ZoneInfo('America/New_York')
        with patch.object(schedules,'LOCAL_ZONE',zone):
            before=datetime(2026,3,8,1,tzinfo=zone).timestamp()
            due=schedules.next_time({'kind':'daily','time':'02:30'},before)
            self.assertEqual(datetime.fromtimestamp(due,zone),datetime(2026,3,9,2,30,tzinfo=zone))
            after_first=datetime(2026,11,1,1,45,tzinfo=zone,fold=0).timestamp()
            due=schedules.next_time({'kind':'daily','time':'01:30'},after_first)
            self.assertEqual(datetime.fromtimestamp(due,zone),datetime(2026,11,2,1,30,tzinfo=zone))

    def test_once_accepts_explicit_offsets_and_rejects_naive_time(self):
        self.assertEqual(schedules.next_time({'kind':'once','at':'2027-01-01T00:00:00+00:00'},0),1798761600)
        with self.assertRaises(ValueError):schedules.next_time({'kind':'once','at':'2027-01-01T00:00:00'},0)
