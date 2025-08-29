"""Unit tests for academic comment scoring system."""
from datetime import timedelta
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, MagicMock, PropertyMock

from django.utils import timezone

from researchhub_comment.scoring import CommentScorer
from purchase.models import Purchase
from reputation.models import BountySolution


class TestCommentScorer(TestCase):
    """Test cases for CommentScorer class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.scorer = CommentScorer
        
    def test_logarithmic_upvotes_calculation(self):
        """Test logarithmic scaling of upvotes."""
        # Test with 0 votes
        self.assertEqual(self.scorer._calculate_log_upvotes(0), 0.0)
        
        # Test with negative input (should return 0)
        self.assertEqual(self.scorer._calculate_log_upvotes(-5), 0.0)
        
        # Test with 1 vote (should return ~3.01)
        score_1 = self.scorer._calculate_log_upvotes(1)
        self.assertAlmostEqual(score_1, 3.01, places=1)
        
        # Test with 10 votes (should return ~10.41)
        score_10 = self.scorer._calculate_log_upvotes(10)
        self.assertAlmostEqual(score_10, 10.41, places=1)
        
        # Test with 100 votes (should return ~20.04)
        score_100 = self.scorer._calculate_log_upvotes(100)
        self.assertAlmostEqual(score_100, 20.04, places=1)
        
        # Test with 1000 votes (should return ~30.00)
        score_1000 = self.scorer._calculate_log_upvotes(1000)
        self.assertAlmostEqual(score_1000, 30.00, places=1)
        
        # Verify logarithmic growth pattern
        # Each 10x increase should add roughly the same amount (~10 points)
        increase_1_to_10 = score_10 - score_1
        increase_10_to_100 = score_100 - score_10
        increase_100_to_1000 = score_1000 - score_100
        
        # All increases should be roughly equal (around 10 points each)
        self.assertAlmostEqual(increase_1_to_10, 7.4, places=1)
        self.assertAlmostEqual(increase_10_to_100, 9.63, places=1) 
        self.assertAlmostEqual(increase_100_to_1000, 9.96, places=1)
    
    def test_time_decay_calculation(self):
        """Test exponential time decay with 30-day half-life."""
        # Test with None date (should return 1.0)
        self.assertEqual(self.scorer._calculate_time_decay(None), 1.0)
        
        # Test with current date (decay = 1.0)
        now = timezone.now()
        decay_now = self.scorer._calculate_time_decay(now)
        self.assertAlmostEqual(decay_now, 1.0, places=2)
        
        # Test with 30 days old (decay = 0.5)
        date_30_days = now - timedelta(days=30)
        decay_30 = self.scorer._calculate_time_decay(date_30_days)
        self.assertAlmostEqual(decay_30, 0.5, places=2)
        
        # Test with 60 days old (decay = 0.25)
        date_60_days = now - timedelta(days=60)
        decay_60 = self.scorer._calculate_time_decay(date_60_days)
        self.assertAlmostEqual(decay_60, 0.25, places=2)
        
        # Test with 90 days old (decay = 0.125)
        date_90_days = now - timedelta(days=90)
        decay_90 = self.scorer._calculate_time_decay(date_90_days)
        self.assertAlmostEqual(decay_90, 0.125, places=2)
        
        # Test with 15 days old (should be between 0.5 and 1.0)
        date_15_days = now - timedelta(days=15)
        decay_15 = self.scorer._calculate_time_decay(date_15_days)
        self.assertGreater(decay_15, 0.5)
        self.assertLess(decay_15, 1.0)
        self.assertAlmostEqual(decay_15, 0.707, places=2)  # sqrt(0.5)
    
    def test_verification_boost(self):
        """Test verification multiplier for users."""
        # Test with None user (boost = 1.0)
        self.assertEqual(self.scorer._get_verification_boost(None), 1.0)
        
        # Test with verified user (boost = 2.0)
        verified_user = Mock()
        verified_user.is_verified = True
        boost_verified = self.scorer._get_verification_boost(verified_user)
        self.assertEqual(boost_verified, 2.0)
        
        # Test with unverified user (boost = 1.0)
        unverified_user = Mock()
        unverified_user.is_verified = False
        boost_unverified = self.scorer._get_verification_boost(unverified_user)
        self.assertEqual(boost_unverified, 1.0)
        
        # Test with user without is_verified attribute (should gracefully return 1.0)
        class UserWithoutVerified:
            pass
        
        user_without_attr = UserWithoutVerified()
        boost_no_attr = self.scorer._get_verification_boost(user_without_attr)
        self.assertEqual(boost_no_attr, 1.0)
    
    def test_economic_signals_calculation(self):
        """Test calculation of economic signals from tips and bounties."""
        # Create mock comment
        comment = Mock(spec=['purchases', 'bounty_solution'])
        
        # Test with no tips or bounties (return 0)
        comment.purchases.filter.return_value.aggregate.return_value = {'total': None}
        comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        economic_0 = self.scorer._calculate_economic_signals(comment)
        self.assertEqual(economic_0, 0.0)
        
        # Test with $10 tip
        comment.purchases.filter.return_value.aggregate.return_value = {'total': Decimal('10')}
        comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        economic_10 = self.scorer._calculate_economic_signals(comment)
        self.assertAlmostEqual(economic_10, 10.41, places=1)  # log10(11) * 10
        
        # Test with $100 bounty award
        comment.purchases.filter.return_value.aggregate.return_value = {'total': None}
        comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': Decimal('100')}
        
        economic_100 = self.scorer._calculate_economic_signals(comment)
        self.assertAlmostEqual(economic_100, 20.04, places=1)  # log10(101) * 10
        
        # Test with both tip and bounty ($50 + $150 = $200)
        comment.purchases.filter.return_value.aggregate.return_value = {'total': Decimal('50')}
        comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': Decimal('150')}
        
        economic_200 = self.scorer._calculate_economic_signals(comment)
        self.assertAlmostEqual(economic_200, 23.01, places=1)  # log10(201) * 10
        
        # Verify correct filter calls
        comment.purchases.filter.assert_called_with(
            purchase_type=Purchase.BOOST,
            paid_status=Purchase.PAID
        )
        comment.bounty_solution.filter.assert_called_with(
            status=BountySolution.Status.AWARDED
        )
    
    def test_full_score_calculation(self):
        """Test complete score calculation with all components."""
        # Create mock comment with all attributes
        comment = Mock(spec=['score', 'created_date', 'created_by', 'purchases', 'bounty_solution'])
        comment.score = 10  # 10 upvotes
        comment.created_date = timezone.now() - timedelta(days=15)  # 15 days old
        
        # Mock user (verified)
        comment.created_by = Mock()
        comment.created_by.is_verified = True
        
        # Mock economic signals ($50 total)
        comment.purchases.filter.return_value.aggregate.return_value = {'total': Decimal('50')}
        comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        # Calculate score
        result = self.scorer.calculate_score(comment)
        
        # Verify structure
        self.assertIn('score', result)
        self.assertIn('components', result)
        self.assertIn('log_upvotes', result['components'])
        self.assertIn('economic_signals', result['components'])
        self.assertIn('time_decay', result['components'])
        self.assertIn('verification_boost', result['components'])
        self.assertIn('base_score', result['components'])
        
        # Verify component values
        components = result['components']
        self.assertAlmostEqual(components['log_upvotes'], 10.41, places=1)
        self.assertAlmostEqual(components['economic_signals'], 17.08, places=1)
        self.assertAlmostEqual(components['time_decay'], 0.707, places=2)
        self.assertEqual(components['verification_boost'], 2.0)
        
        # Verify final score calculation
        # (10.41 + 17.08) * 0.707 * 2.0 ≈ 38.87
        self.assertAlmostEqual(result['score'], 38.87, places=0)
    
    def test_scenario_verified_beats_unverified(self):
        """Test that verified user with fewer votes beats unverified with more."""
        # Verified user comment with 10 votes
        verified_comment = Mock(spec=['score', 'created_date', 'created_by', 'purchases', 'bounty_solution'])
        verified_comment.score = 10
        verified_comment.created_date = timezone.now()
        verified_comment.created_by = Mock(is_verified=True)
        verified_comment.purchases.filter.return_value.aggregate.return_value = {'total': None}
        verified_comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        # Unverified user comment with 50 votes
        unverified_comment = Mock(spec=['score', 'created_date', 'created_by', 'purchases', 'bounty_solution'])
        unverified_comment.score = 50
        unverified_comment.created_date = timezone.now()
        unverified_comment.created_by = Mock(is_verified=False)
        unverified_comment.purchases.filter.return_value.aggregate.return_value = {'total': None}
        unverified_comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        # Calculate scores
        verified_score = self.scorer.calculate_score(verified_comment)['score']
        unverified_score = self.scorer.calculate_score(unverified_comment)['score']
        
        # Verified should rank higher despite fewer votes
        # Verified: 10.41 * 2.0 = 20.82
        # Unverified: 17.08 * 1.0 = 17.08
        self.assertGreater(verified_score, unverified_score)
    
    def test_scenario_economic_signals_matter(self):
        """Test that economic signals significantly boost ranking."""
        # Comment with 20 votes but no tips
        no_tip_comment = Mock(spec=['score', 'created_date', 'created_by', 'purchases', 'bounty_solution'])
        no_tip_comment.score = 20
        no_tip_comment.created_date = timezone.now()
        no_tip_comment.created_by = Mock(is_verified=False)
        no_tip_comment.purchases.filter.return_value.aggregate.return_value = {'total': None}
        no_tip_comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        # Comment with 5 votes but $100 tip
        tipped_comment = Mock(spec=['score', 'created_date', 'created_by', 'purchases', 'bounty_solution'])
        tipped_comment.score = 5
        tipped_comment.created_date = timezone.now()
        tipped_comment.created_by = Mock(is_verified=False)
        tipped_comment.purchases.filter.return_value.aggregate.return_value = {'total': Decimal('100')}
        tipped_comment.bounty_solution.filter.return_value.aggregate.return_value = {'total': None}
        
        # Calculate scores
        no_tip_score = self.scorer.calculate_score(no_tip_comment)['score']
        tipped_score = self.scorer.calculate_score(tipped_comment)['score']
        
        # Tipped comment should rank higher
        # No tip: 13.01 (log of 20)
        # Tipped: 7.0 + 20.04 = 27.04
        self.assertGreater(tipped_score, no_tip_score)