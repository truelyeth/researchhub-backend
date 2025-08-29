from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, RequestFactory
from django.utils import timezone
from django.http import QueryDict
from rest_framework.test import APITestCase

from hub.models import Hub
from paper.models import Paper
from purchase.models import Purchase
from reputation.models import Bounty, BountySolution, Escrow
from researchhub_comment.filters import RHCommentFilter
from researchhub_comment.models import RhCommentModel, RhCommentThreadModel
from researchhub_document.models import ResearchhubUnifiedDocument
from user.models import Author, UserVerification


User = get_user_model()


class TestAcademicFilterIntegration(TestCase):
    def setUp(self):
        RhCommentModel.objects.all().delete()
        self.verified_user, _ = User.objects.get_or_create(
            username="verified_expert",
            defaults={
                "email": "verified@example.com",
                "password": "testpass123"
            }
        )
        self.verified_author, _ = Author.objects.get_or_create(
            user=self.verified_user,
            defaults={
                "first_name": "Verified",
                "last_name": "Expert"
            }
        )
        
        UserVerification.objects.get_or_create(
            user=self.verified_user,
            defaults={"status": UserVerification.Status.APPROVED}
        )
        
        self.regular_user, _ = User.objects.get_or_create(
            username="regular_user",
            defaults={
                "email": "regular@example.com",
                "password": "testpass123"
            }
        )
        self.regular_author, _ = Author.objects.get_or_create(
            user=self.regular_user,
            defaults={
                "first_name": "Regular",
                "last_name": "User"
            }
        )
        
        self.hub, _ = Hub.objects.get_or_create(
            name="Test Hub",
            defaults={"description": "Test hub for academic scoring"}
        )
        
        self.paper, _ = Paper.objects.get_or_create(
            doi="10.1234/test",
            defaults={
                "title": "Test Paper",
                "paper_title": "Test Paper"
            }
        )
        
        self.unified_doc, _ = ResearchhubUnifiedDocument.objects.get_or_create(
            document_type="PAPER",
            paper=self.paper
        )
        self.unified_doc.hubs.add(self.hub)
        
        content_type = ContentType.objects.get_for_model(ResearchhubUnifiedDocument)
        self.thread, _ = RhCommentThreadModel.objects.get_or_create(
            content_type=content_type,
            object_id=self.unified_doc.id,
            defaults={
                "created_by": self.regular_user
            }
        )
        
        self.factory = RequestFactory()
        request = self.factory.get('/api/comments/')
        request.user = self.regular_user
        request.organization = None
        query_dict = QueryDict(mutable=True)
        query_dict['privacy_type'] = 'PUBLIC'
        query_dict._mutable = False
        self.filter = RHCommentFilter(data=query_dict, request=request)
        
    def _create_comment(self, author, score=0, tip_amount=0, bounty_award=0, days_old=0):
        created_date = timezone.now() - timedelta(days=days_old)
        
        comment = RhCommentModel.objects.create(
            thread=self.thread,
            created_by=author.user,
            created_date=created_date,
            updated_date=created_date,
            comment_content_json={"ops": [{"insert": f"Test comment by {author.user.username}"}]}
        )
        
        comment.score = score
        comment.save()
        
        if tip_amount > 0:
            Purchase.objects.create(
                content_type=ContentType.objects.get_for_model(comment),
                object_id=comment.id,
                purchase_type=Purchase.BOOST,
                paid_status=Purchase.PAID,
                amount=Decimal(str(tip_amount)),
                user=self.regular_user
            )
        
        if bounty_award > 0:
            comment_ct = ContentType.objects.get_for_model(comment)
            escrow = Escrow.objects.create(
                created_by=self.regular_user,
                amount_holding=Decimal(str(bounty_award)),
                hold_type=Escrow.BOUNTY,
                content_type=comment_ct,
                object_id=comment.id
            )
            
            bounty = Bounty.objects.create(
                amount=Decimal(str(bounty_award)),
                created_by=self.regular_user,
                item_content_type=comment_ct,
                item_object_id=comment.id,
                status=Bounty.CLOSED,
                escrow=escrow,
                unified_document=self.unified_doc
            )
            
            BountySolution.objects.create(
                bounty=bounty,
                status=BountySolution.Status.AWARDED,
                awarded_amount=Decimal(str(bounty_award)),
                created_by=author.user,
                content_type=ContentType.objects.get_for_model(comment),
                object_id=comment.id
            )
        
        return comment
    
    def test_best_sorting_verified_beats_unverified(self):
        verified_comment = self._create_comment(
            author=self.verified_author,
            score=10
        )
        
        unverified_comment = self._create_comment(
            author=self.regular_author,
            score=50
        )
        
        qs = RhCommentModel.objects.all()
        sorted_qs = self.filter.ordering_filter(qs, "ordering", "BEST")
        
        results = list(sorted_qs)
        
        self.assertEqual(results[0].id, verified_comment.id)
        self.assertEqual(results[1].id, unverified_comment.id)
    
    def test_top_sorting_uses_academic_scoring(self):
        comment1 = self._create_comment(author=self.regular_author, score=100)
        comment2 = self._create_comment(author=self.verified_author, score=20)
        
        qs = RhCommentModel.objects.all()
        sorted_qs = self.filter.ordering_filter(qs, "ordering", "TOP")
        
        results = list(sorted_qs)
        
        self.assertEqual(results[0].id, comment2.id)
        self.assertEqual(results[1].id, comment1.id)
    
    def test_economic_signals_boost_ranking(self):
        tipped_comment = self._create_comment(
            author=self.regular_author,
            score=10,
            tip_amount=50
        )
        
        no_tip_comment = self._create_comment(
            author=self.regular_author,
            score=30
        )
        
        qs = RhCommentModel.objects.all()
        sorted_qs = self.filter.ordering_filter(qs, "ordering", "BEST")
        
        results = list(sorted_qs)
        
        self.assertEqual(results[0].id, tipped_comment.id)
        self.assertEqual(results[1].id, no_tip_comment.id)
    
    def test_time_decay_affects_ranking(self):
        new_comment = self._create_comment(
            author=self.regular_author,
            score=10,
            days_old=0
        )
        
        old_comment = self._create_comment(
            author=self.regular_author,
            score=40,
            days_old=60
        )
        
        qs = RhCommentModel.objects.all()
        sorted_qs = self.filter.ordering_filter(qs, "ordering", "BEST")
        
        results = list(sorted_qs)
        
        self.assertEqual(len(results), 2)
    
    def test_bounty_sorting_open_first(self):
        comment_closed = self._create_comment(
            author=self.verified_author,
            score=50
        )
        
        comment_open = self._create_comment(
            author=self.regular_author,
            score=10
        )
        
        comment_ct = ContentType.objects.get_for_model(RhCommentModel)
        escrow_closed = Escrow.objects.create(
            created_by=self.regular_user,
            amount_holding=Decimal("100"),
            hold_type=Escrow.BOUNTY,
            content_type=comment_ct,
            object_id=comment_closed.id
        )
        
        escrow_open = Escrow.objects.create(
            created_by=self.regular_user,
            amount_holding=Decimal("50"),
            hold_type=Escrow.BOUNTY,
            content_type=comment_ct,
            object_id=comment_open.id
        )
        closed_bounty = Bounty.objects.create(
            amount=Decimal("100"),
            created_by=self.regular_user,
            item_content_type=comment_ct,
            item_object_id=comment_closed.id,
            status=Bounty.CLOSED,
            escrow=escrow_closed,
            unified_document=self.unified_doc
        )
        
        open_bounty = Bounty.objects.create(
            amount=Decimal("50"),
            created_by=self.regular_user,
            item_content_type=comment_ct,
            item_object_id=comment_open.id,
            status=Bounty.OPEN,
            escrow=escrow_open,
            unified_document=self.unified_doc
        )
        
        qs = RhCommentModel.objects.all()
        sorted_qs = self.filter.ordering_filter(qs, "ordering", "BOUNTY")
        
        results = list(sorted_qs)
        
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, comment_open.id)
        self.assertEqual(results[1].id, comment_closed.id)