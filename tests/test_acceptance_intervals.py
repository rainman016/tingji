import unittest

from validate_real_video import uncovered_intervals


class AcceptanceIntervalTests(unittest.TestCase):
    def test_gap_inside_long_speech_is_not_hidden_by_aggregate_coverage(self):
        self.assertEqual(uncovered_intervals([(0, 10000)], [(0, 4000), (6000, 10000)]), [(4000, 6000)])

    def test_overlapping_transcript_ranges_do_not_create_false_gaps(self):
        self.assertEqual(uncovered_intervals([(1000, 9000)], [(0, 4000), (3000, 10000)]), [])

    def test_leading_tail_and_separate_speech_regions_are_bounded(self):
        self.assertEqual(uncovered_intervals([(1000, 4000), (7000, 9000)], [(2000, 3500), (7000, 8000)]),
                         [(1000, 2000), (3500, 4000), (8000, 9000)])
