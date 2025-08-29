import math
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from django.db.models import Q, Sum, DecimalField
from django.db.models.functions import Cast
from django.utils import timezone

from purchase.models import Purchase
from reputation.models import BountySolution


class CommentScorer:
    HALF_LIFE_DAYS = 30
    VERIFICATION_MULTIPLIER = 2.0
    LOG_BASE_MULTIPLIER = 10
    
    @classmethod
    def calculate_score(cls, comment) -> Dict[str, float]:
        log_upvotes = cls._calculate_log_upvotes(comment.score)
        economic_signals = cls._calculate_economic_signals(comment)
        time_decay = cls._calculate_time_decay(comment.created_date)
        verification_boost = cls._get_verification_boost(comment.created_by)
        
        base_score = log_upvotes + economic_signals
        academic_score = base_score * time_decay * verification_boost
        
        return {
            'score': academic_score,
            'components': {
                'log_upvotes': log_upvotes,
                'economic_signals': economic_signals,
                'time_decay': time_decay,
                'verification_boost': verification_boost,
                'base_score': base_score
            }
        }
    
    @classmethod
    def _calculate_log_upvotes(cls, score: int) -> float:
        if score <= 0:
            return 0.0
        
        return cls.LOG_BASE_MULTIPLIER * math.log10(score + 1)
    
    @classmethod
    def _calculate_economic_signals(cls, comment) -> float:
        if hasattr(comment, 'tip_amount') and hasattr(comment, 'bounty_award_amount'):
            tips = comment.tip_amount or Decimal('0')
            bounty_awards = comment.bounty_award_amount or Decimal('0')
        else:
            tips = comment.purchases.filter(
                purchase_type=Purchase.BOOST,
                paid_status=Purchase.PAID
            ).aggregate(
                total=Sum(Cast('amount', DecimalField(max_digits=19, decimal_places=10)))
            )['total'] or Decimal('0')
            
            bounty_awards = comment.bounty_solution.filter(
                status=BountySolution.Status.AWARDED
            ).aggregate(
                total=Sum('awarded_amount')
            )['total'] or Decimal('0')
        
        total_economic = float(tips) + float(bounty_awards)
        
        if total_economic > 0:
            return math.log10(total_economic + 1) * 10
        
        return 0.0
    
    @classmethod
    def _calculate_time_decay(cls, created_date: Optional[datetime]) -> float:
        if not created_date:
            return 1.0
        
        now = timezone.now()
        age = now - created_date
        days_old = age.total_seconds() / 86400
        
        decay_factor = math.pow(0.5, days_old / cls.HALF_LIFE_DAYS)
        
        return decay_factor
    
    @classmethod
    def _get_verification_boost(cls, user) -> float:
        if not user:
            return 1.0
        
        try:
            if user.is_verified:
                return cls.VERIFICATION_MULTIPLIER
        except Exception:
            pass
        
        return 1.0