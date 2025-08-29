from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Hub
from paper.models import Paper
from purchase.models import Purchase
from reputation.models import Escrow, Bounty
from researchhub_comment.models import RhCommentModel, RhCommentThreadModel
from researchhub_document.models import ResearchhubUnifiedDocument
from user.models import Author, UserVerification


User = get_user_model()


class TestAcademicCommentAPI(APITestCase):
    def setUp(self):
        RhCommentModel.objects.all().delete()
        self.verified_user, _ = User.objects.get_or_create(
            username="verified_api",
            defaults={
                "email": "verified_api@test.com",
                "password": "testpass123"
            }
        )
        self.verified_author, _ = Author.objects.get_or_create(
            user=self.verified_user,
            defaults={
                "first_name": "Verified",
                "last_name": "User"
            }
        )
        UserVerification.objects.get_or_create(
            user=self.verified_user,
            defaults={
                "status": UserVerification.Status.APPROVED,
                "first_name": "Verified",
                "last_name": "User",
                "verified_by": UserVerification.Type.MANUAL,
                "external_id": "test123"
            }
        )
        
        self.regular_user, _ = User.objects.get_or_create(
            username="regular_api",
            defaults={
                "email": "regular_api@test.com",
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
            name="Test API Hub",
            defaults={"description": "Hub for API testing"}
        )
        
        self.paper, _ = Paper.objects.get_or_create(
            doi="10.1234/test_api",
            defaults={
                "title": "Test API Paper",
                "paper_title": "Test API Paper"
            }
        )
        
        self.unified_doc, _ = ResearchhubUnifiedDocument.objects.get_or_create(
            document_type="PAPER",
            paper=self.paper
        )
        self.unified_doc.hubs.add(self.hub)
        
        from django.contrib.contenttypes.models import ContentType
        content_type = ContentType.objects.get_for_model(ResearchhubUnifiedDocument)
        self.thread, _ = RhCommentThreadModel.objects.get_or_create(
            content_type=content_type,
            object_id=self.unified_doc.id,
            defaults={
                "created_by": self.regular_user
            }
        )
    
    def _create_comment(self, user, score=0, days_old=0):
        created_date = timezone.now() - timedelta(days=days_old)
        comment = RhCommentModel.objects.create(
            thread=self.thread,
            created_by=user,
            created_date=created_date,
            updated_date=created_date,
            comment_content_json={"ops": [{"insert": f"Comment by {user.username}"}]}
        )
        comment.score = score
        comment.save()
        return comment
    
    def test_best_sorting_api_endpoint(self):
        verified_comment = self._create_comment(self.verified_user, score=10)
        regular_comment = self._create_comment(self.regular_user, score=50)
        
        self.client.force_authenticate(user=self.regular_user)
        
        url = f"/api/paper/{self.paper.id}/comments/?ordering=BEST"
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        from researchhub_comment.filters import RHCommentFilter
        from django.http import QueryDict
        from django.test import RequestFactory
        
        factory = RequestFactory()
        request = factory.get('/api/comments/')
        request.user = self.regular_user
        request.organization = None
        
        qs = RhCommentModel.objects.filter(thread=self.thread)
        query_dict = QueryDict('ordering=BEST&privacy_type=PUBLIC')
        filter_instance = RHCommentFilter(data=query_dict, queryset=qs, request=request)
        filtered_qs = filter_instance.qs
        
        ordered_qs = filter_instance.ordering_filter(filtered_qs, "ordering", "BEST")
        results = list(ordered_qs)
        
        self.assertGreaterEqual(len(results), 2)
        verified_idx = next(i for i, c in enumerate(results) if c.id == verified_comment.id)
        regular_idx = next(i for i, c in enumerate(results) if c.id == regular_comment.id)
        self.assertLess(verified_idx, regular_idx)
    
    def test_top_sorting_api_endpoint(self):
        old_comment = self._create_comment(self.regular_user, score=100, days_old=60)
        new_comment = self._create_comment(self.regular_user, score=20, days_old=0)
        
        self.client.force_authenticate(user=self.regular_user)
        url = f"/api/paper/{self.paper.id}/comments/?ordering=TOP"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
