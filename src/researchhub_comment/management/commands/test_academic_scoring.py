from datetime import timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
import random

from hub.models import Hub
from paper.models import Paper
from researchhub_document.models import ResearchhubUnifiedDocument, ResearchhubPost
from researchhub_comment.models import RhCommentModel, RhCommentThreadModel
from user.models import Author, UserVerification
from purchase.models import Purchase

User = get_user_model()

class Command(BaseCommand):
    help = 'Creates test data to demonstrate academic comment scoring differences'

    def handle(self, *args, **options):
        # Clean up old test data
        self.stdout.write("Cleaning up old test data...")
        try:
            # Since username is set to email automatically, we need to filter by email
            User.objects.filter(email__in=[f"test_expert{i}@scoring.com" for i in range(10)]).delete()
            User.objects.filter(email__in=[f"test_regular{i}@scoring.com" for i in range(10)]).delete()
            ResearchhubUnifiedDocument.objects.filter(title__startswith="SCORING_TEST_").delete()
        except Exception as e:
            self.stdout.write(f"Cleanup warning: {e}")
        
        # Create multiple users
        users = {}
        
        # Create verified experts
        for i in range(3):
            user, created = User.objects.get_or_create(
                email=f"test_expert{i}@scoring.com",
                defaults={
                    "username": f"test_expert{i}@scoring.com",
                    "first_name": f"Expert",
                    "last_name": f"User{i}"
                }
            )
            if not created:
                user.first_name = f"Expert"
                user.last_name = f"User{i}"
                user.save()
            author, _ = Author.objects.get_or_create(
                user=user,
                defaults={
                    "first_name": f"Expert",
                    "last_name": f"User{i}"
                }
            )
            UserVerification.objects.get_or_create(
                user=user,
                defaults={
                    "status": UserVerification.Status.APPROVED,
                    "first_name": f"Expert",
                    "last_name": f"User{i}",
                    "verified_by": UserVerification.Type.MANUAL,
                    "external_id": f"expert{i}"
                }
            )
            users[f"expert_{i}"] = user
            self.stdout.write(f"✓ Created verified expert: {user.username}")
        
        # Create regular users
        for i in range(5):
            user, created = User.objects.get_or_create(
                email=f"test_regular{i}@scoring.com",
                defaults={
                    "username": f"test_regular{i}@scoring.com",
                    "first_name": f"Regular",
                    "last_name": f"User{i}"
                }
            )
            if not created:
                user.first_name = f"Regular"
                user.last_name = f"User{i}"
                user.save()
            author, _ = Author.objects.get_or_create(
                user=user,
                defaults={
                    "first_name": f"Regular",
                    "last_name": f"User{i}"
                }
            )
            users[f"regular_{i}"] = user
            self.stdout.write(f"✓ Created regular user: {user.username}")
        
        # Create unified document directly without Paper due to schema issues
        hub = Hub.objects.first() or Hub.objects.create(name="Science", description="General Science")
        
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        
        # Create unified document first
        unified_doc = ResearchhubUnifiedDocument.objects.create(
            document_type="DISCUSSION"
        )
        
        # Create a ResearchhubPost linked to the unified document
        post = ResearchhubPost.objects.create(
            title=f"SCORING_TEST_{unique_id}: Academic Comment Sorting Demo",
            renderable_text="This is a test post to demonstrate academic comment scoring differences.",
            created_by=users["regular_0"],
            unified_document=unified_doc
        )
        
        unified_doc.hubs.add(hub)
        
        thread = RhCommentThreadModel.objects.create(
            content_object=post,
            created_by=users["regular_0"]
        )
        
        self.stdout.write(f"\n✓ Created test post: {post.title}")
        self.stdout.write(f"  Document ID: {unified_doc.id}")
        
        self.stdout.write("\n=== Creating Test Comments ===\n")
        
        comments = []
        comment_ct = ContentType.objects.get_for_model(RhCommentModel)
        
        # Scenario 1: Expert with moderate votes beats popular regular user
        self.stdout.write("Scenario 1: Verified Expert vs Popular Regular User")
        
        c1 = self._create_comment(
            thread, users["expert_0"], 
            "I'm a verified expert with solid research backing this claim. The methodology shows...",
            score=15, days_ago=2
        )
        comments.append(("Expert with 15 votes (2 days old)", c1))
        
        c2 = self._create_comment(
            thread, users["regular_0"],
            "Great paper! I totally agree with everything. This is amazing work!",
            score=75, days_ago=2
        )
        comments.append(("Regular user with 75 votes (2 days old)", c2))
        
        # Scenario 2: Economic signals (tips) boost ranking
        self.stdout.write("\nScenario 2: Tips vs Pure Upvotes")
        
        c3 = self._create_comment(
            thread, users["regular_1"],
            "This finding contradicts Smith et al. (2023). Here's my detailed analysis with citations...",
            score=8, days_ago=1
        )
        # Add $100 tip
        Purchase.objects.create(
            content_type=comment_ct,
            object_id=c3.id,
            purchase_type=Purchase.BOOST,
            paid_status=Purchase.PAID,
            amount="100",
            user=users["regular_2"]
        )
        comments.append(("8 votes + $100 tip (1 day old)", c3))
        
        c4 = self._create_comment(
            thread, users["regular_2"],
            "Nice paper!",
            score=40, days_ago=1
        )
        comments.append(("40 votes, no tip (1 day old)", c4))
        
        # Scenario 3: Time decay effect
        self.stdout.write("\nScenario 3: Time Decay - Old vs New")
        
        c5 = self._create_comment(
            thread, users["regular_3"],
            "This was groundbreaking when first published. Still holds up today!",
            score=200, days_ago=90
        )
        comments.append(("200 votes but 90 days old", c5))
        
        c6 = self._create_comment(
            thread, users["expert_1"],
            "Recent developments in the field support this. See latest Nature paper...",
            score=25, days_ago=0
        )
        comments.append(("Expert with 25 votes (posted today)", c6))
        
        # Skipping bounty scenarios due to database schema issues
        self.stdout.write("\nAdding variety comments...")
        
        # Medium popularity regular comments
        for i in range(3, 5):
            c = self._create_comment(
                thread, users[f"regular_{i}"],
                f"Interesting perspective on the {i}th hypothesis. I wonder if...",
                score=random.randint(10, 30),
                days_ago=random.randint(0, 10)
            )
            comments.append((f"Regular comment {i-2} ({c.score} votes)", c))
        
        # Calculate and display academic scores
        self.stdout.write("\n=== Academic Scores Breakdown ===\n")
        
        from researchhub_comment.scoring import CommentScorer
        
        scored_comments = []
        for desc, comment in comments:
            score_data = CommentScorer.calculate_score(comment)
            scored_comments.append((desc, comment, score_data))
        
        # Sort by academic score
        scored_comments.sort(key=lambda x: x[2]['score'], reverse=True)
        
        self.stdout.write("BEST/TOP Ordering (by academic score):")
        for i, (desc, comment, score_data) in enumerate(scored_comments, 1):
            self.stdout.write(
                f"\n{i}. {desc}\n"
                f"   Academic Score: {score_data['score']:.2f}\n"
                f"   - Upvotes: {comment.score} → Log score: {score_data['components']['log_upvotes']:.2f}\n"
                f"   - Economic: ${score_data['components']['economic_signals']:.2f}\n"
                f"   - Time decay: {score_data['components']['time_decay']:.3f}\n"
                f"   - Verification: {score_data['components']['verification_boost']}x"
            )
        
        self.stdout.write("\n" + "="*60)
        self.stdout.write(f"\n✅ Test data created successfully!\n")
        self.stdout.write(f"📄 Post: '{post.title}'")
        self.stdout.write(f"🔗 Document ID: {unified_doc.id}")
        self.stdout.write(f"💬 Total comments: {len(comments)}")
        self.stdout.write(f"\n🔍 View in browser:")
        self.stdout.write(f"   http://localhost:3000/post/{unified_doc.id}")
        self.stdout.write(f"\n📊 API endpoints to test:")
        self.stdout.write(f"   - BEST: /api/researchhub_unified_document/{unified_doc.id}/comments/?ordering=BEST")
        self.stdout.write(f"   - TOP: /api/researchhub_unified_document/{unified_doc.id}/comments/?ordering=TOP")
        self.stdout.write(f"   - OLD BEHAVIOR: /api/researchhub_unified_document/{unified_doc.id}/comments/?ordering=CREATED_DATE")
        
    def _create_comment(self, thread, user, text, score=0, days_ago=0):
        created_date = timezone.now() - timedelta(days=days_ago)
        comment = RhCommentModel.objects.create(
            thread=thread,
            created_by=user,
            comment_content_json={"ops": [{"insert": text}]},
            created_date=created_date,
            updated_date=created_date
        )
        comment.score = score
        comment.save()
        return comment