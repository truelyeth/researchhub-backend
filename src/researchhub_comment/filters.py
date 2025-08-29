from functools import reduce

from django.contrib.contenttypes.models import ContentType
from django.db.models import (
    DecimalField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Sum,
    Case,
    When,
    F,
    Value,
)
from django.db.models.functions import Cast, Coalesce
from django_filters import DateTimeFilter
from django_filters import rest_framework as filters

from purchase.models import Purchase
from reputation.models import Bounty, BountySolution
from user.models import User, UserVerification
from researchhub_access_group.constants import PRIVATE, PUBLIC, WORKSPACE
from researchhub_comment.scoring import CommentScorer
from researchhub_comment.constants.rh_comment_thread_types import (
    AUTHOR_UPDATE,
    GENERIC_COMMENT,
    INNER_CONTENT_COMMENT,
    RH_COMMENT_THREAD_TYPES,
    SUMMARY,
)
from researchhub_comment.models import RhCommentModel
from utils.http import GET

BEST = "BEST"
TOP = "TOP"
BOUNTY = "BOUNTY"
REVIEW = "REVIEW"
PEER_REVIEW = "PEER_REVIEW"
DISCUSSION = "DISCUSSION"
REPLICABILITY_COMMENT = "REPLICABILITY_COMMENT"
CREATED_DATE = "CREATED_DATE"
ASCENDING_TRUE = "TRUE"
ASCENDING_FALSE = "FALSE"

ORDER_CHOICES = (
    (BEST, "Best"),
    (TOP, "Top"),
    (CREATED_DATE, "Created Date"),
)

PRIVACY_CHOICES = (
    (PUBLIC, "Public comments"),
    (PRIVATE, "Private comments"),
    (WORKSPACE, "Organization comments"),
)

FILTER_CHOICES = (
    (BOUNTY, "Has Bounty"),
    (REVIEW, REVIEW),
    (PEER_REVIEW, PEER_REVIEW),
    (DISCUSSION, DISCUSSION),
    (REPLICABILITY_COMMENT, REPLICABILITY_COMMENT),
    (INNER_CONTENT_COMMENT, INNER_CONTENT_COMMENT),
    (AUTHOR_UPDATE, AUTHOR_UPDATE),
)


class RHCommentFilter(filters.FilterSet):
    created_date__gte = DateTimeFilter(
        field_name="created_date",
        lookup_expr="gte",
    )
    created_date__lt = DateTimeFilter(
        field_name="created_date",
        lookup_expr="lt",
    )
    updated_date__gte = DateTimeFilter(
        field_name="updated_date",
        lookup_expr="gte",
    )
    updated_date__lt = DateTimeFilter(
        field_name="updated_date",
        lookup_expr="lt",
    )
    ordering = filters.ChoiceFilter(
        method="ordering_filter",
        choices=ORDER_CHOICES,
        null_value=BEST,
        label="Ordering",
    )
    filtering = filters.ChoiceFilter(
        method="filtering_filter",
        choices=FILTER_CHOICES,
        label="Filter by",
    )
    child_count = filters.NumberFilter(
        method="filter_child_count",
        label="Child Comment Count",
    )
    thread_type = filters.ChoiceFilter(
        choices=RH_COMMENT_THREAD_TYPES,
        field_name="thread__thread_type",
        label="Thread Type",
    )
    privacy_type = filters.ChoiceFilter(
        choices=PRIVACY_CHOICES, method="privacy_filter", label="Privacy Filter"
    )
    parent__isnull = filters.BooleanFilter(
        field_name="parent__isnull", method="filtering_parent"
    )

    class Meta:
        model = RhCommentModel
        fields = ("ordering",)

    def __init__(self, *args, request=None, **kwargs):
        # Privacy type should always be set, even if not passed in
        # This will ensure private/organization comments will be hidden
        if request.method == GET:
            kwargs["data"]._mutable = True
            if "privacy_type" not in kwargs["data"]:
                kwargs["data"]["privacy_type"] = PUBLIC
            kwargs["data"]._mutable = False
        super().__init__(*args, request=request, **kwargs)

    def _is_ascending(self):
        return self.data.get("ascending", ASCENDING_FALSE) == ASCENDING_TRUE

    def _get_ordering_keys(self, keys):
        if not self._is_ascending():
            return [f"-{key}" for key in keys]
        return keys

    def _annotate_bounty_sum(self, qs, annotation_filters=None):
        annotation_filters = [] if annotation_filters is None else annotation_filters
        annotation_filters_query = reduce(
            lambda q, value: q | Q(**value), annotation_filters, Q()
        )
        queryset = qs.annotate(
            bounty_sum=Coalesce(
                Sum("bounties__amount", filter=annotation_filters_query),
                0,
                output_field=DecimalField(),
            )
        )
        return queryset

    def _annotate_academic_score_components(self, qs):
        qs = qs.annotate(
            tip_amount=Coalesce(
                Sum(
                    Cast("purchases__amount", DecimalField(max_digits=19, decimal_places=10)),
                    filter=Q(
                        purchases__purchase_type=Purchase.BOOST,
                        purchases__paid_status=Purchase.PAID,
                    ),
                ),
                Value(0),
                output_field=DecimalField(max_digits=19, decimal_places=10),
            )
        )
        
        qs = qs.annotate(
            bounty_award_amount=Coalesce(
                Sum(
                    "bounty_solution__awarded_amount",
                    filter=Q(bounty_solution__status=BountySolution.Status.AWARDED),
                ),
                Value(0),
                output_field=DecimalField(max_digits=19, decimal_places=10),
            )
        )
        
        qs = qs.annotate(
            is_verified_user=Exists(
                User.objects.filter(
                    id=OuterRef("created_by_id"),
                    userverification__status=UserVerification.Status.APPROVED
                )
            )
        )
        
        return qs

    def _apply_academic_ordering(self, qs):
        qs = self._annotate_academic_score_components(qs)
        qs = qs.select_related(
            "created_by",
            "created_by__userverification"
        ).prefetch_related(
            "purchases",
            "bounty_solution"
        )
        
        comments = list(qs)
        
        comment_scores = []
        for comment in comments:
            score_data = CommentScorer.calculate_score(comment)
            comment.academic_score_calculated = score_data["score"]
            comment_scores.append((comment.id, score_data["score"]))
        
        comment_scores.sort(key=lambda x: x[1], reverse=True)
        
        sorted_ids = [item[0] for item in comment_scores]
        
        if not sorted_ids:
            return qs.none()
        
        preserved = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(sorted_ids)]
        )
        return qs.filter(id__in=sorted_ids).order_by(preserved)

    def _is_on_child_queryset(self):
        instance_class_name = self.queryset.__class__.__name__
        if instance_class_name == "RelatedManager":
            return True

    def _has_explicit_filtering(self):
        """
        Return True if an explicit `filtering` parameter was supplied by the client.
        """
        return bool(self.data.get("filtering"))

    @property
    def qs(self):
        """
        Override the default queryset evaluation.
        It applies a *default* filter when the client has not explicitly provided
        the ``filtering`` query parameter.
        The default behaviour should return only GENERIC_COMMENT comments that do
        **not** have any bounties attached. This excludes REVIEW / PEER_REVIEW
        comments and comments with bounties.
        This keeps the response focused on general discussion by default.
        """
        # Start with the base queryset that Django-Filters builds using the
        # declared filters (ordering, privacy, explicit filtering, etc.).
        base_qs = super().qs

        # If we're on a RelatedManager (children queryset) or the caller explicitly
        # requested a filtering, respect that request and return the queryset
        # unmodified.
        if self._is_on_child_queryset() or self._has_explicit_filtering():
            return base_qs

        # Apply the default restriction: include only comments whose own
        # `comment_type` and their parent thread's `thread_type` are both
        # GENERIC_COMMENT, and that do **not** have bounties attached.
        return base_qs.filter(
            comment_type=GENERIC_COMMENT,
            thread__thread_type=GENERIC_COMMENT,
            bounties__isnull=True,
            parent__isnull=True,
        )

    def ordering_filter(self, qs, name, value):
        if value == BEST:
            qs = self._apply_academic_ordering(qs)
        elif value == TOP:
            qs = self._apply_academic_ordering(qs)
        elif value == BOUNTY:
            qs = self._annotate_bounty_sum(qs).filter(bounty_sum__gt=0)
            
            comment_ct = ContentType.objects.get_for_model(RhCommentModel)
            
            qs = qs.annotate(
                has_open_bounty=Exists(
                    Bounty.objects.filter(
                        item_content_type=comment_ct,
                        item_object_id=OuterRef("id"),
                        status=Bounty.OPEN,
                    )
                )
            )
            
            qs = self._apply_academic_ordering(qs)
            
            sorted_comments = list(qs)
            
            open_bounty_comments = []
            closed_bounty_comments = []
            
            for comment in sorted_comments:
                if getattr(comment, "has_open_bounty", False):
                    open_bounty_comments.append(comment.id)
                else:
                    closed_bounty_comments.append(comment.id)
            
            final_order = open_bounty_comments + closed_bounty_comments
            
            if not final_order:
                return qs.none()
            
            preserved = Case(
                *[When(pk=pk, then=pos) for pos, pk in enumerate(final_order)]
            )
            qs = qs.filter(id__in=final_order).order_by(preserved)
            
        elif value == CREATED_DATE:
            keys = self._get_ordering_keys(["created_date"])
            qs = qs.order_by(*keys)
        return qs

    def filtering_filter(self, qs, name, value):
        if self._is_on_child_queryset():
            return qs

        if value == BOUNTY:
            qs = qs.filter(bounties__isnull=False)
            qs = self._annotate_bounty_sum(
                qs, annotation_filters=[{"bounties__status": Bounty.OPEN}]
            )
        elif value == REVIEW:
            qs = qs.filter(comment_type__in=[REVIEW, PEER_REVIEW])
        elif value == INNER_CONTENT_COMMENT:
            qs = qs.filter(comment_type=INNER_CONTENT_COMMENT)
        elif value == DISCUSSION:
            qs = qs.filter(
                (Q(comment_type=GENERIC_COMMENT) & Q(bounties__isnull=True))
                | Q(comment_type=SUMMARY)
                | Q(comment_type=INNER_CONTENT_COMMENT)
            )
        elif value == REPLICABILITY_COMMENT:
            qs = qs.filter(thread__thread_type=REPLICABILITY_COMMENT)
        elif value == "AUTHOR_UPDATE":
            qs = qs.filter(thread__thread_type=AUTHOR_UPDATE)

        return qs

    def filter_child_count(self, qs, name, value):
        if not self._is_on_child_queryset():
            return qs
        offset = int(self.data.get("child_offset", 0))
        count = offset + value

        # Returning the slice qs[offset:count] will cause an error
        # if the queryset has additional filtering
        sliced_children_ids = qs[offset:count].values_list("id")
        return qs.filter(id__in=sliced_children_ids)

    def privacy_filter(self, qs, name, value):
        request = self.request
        user = request.user

        if user.is_anonymous:
            return qs.filter(thread__permissions__isnull=True)

        if value == PRIVATE:
            qs = qs.filter(
                thread__permissions__user=user,
                thread__permissions__organization__isnull=True,
            )
        elif value == WORKSPACE:
            # Organization permission check is done in permissions
            org = request.organization
            qs = qs.filter(
                thread__permissions__organization=org,
                thread__permissions__organization__isnull=False,
            )
        else:
            # Public comments
            qs = qs.filter(thread__permissions__isnull=True)
        return qs

    def filtering_parent(self, qs, name, value):
        if self._is_on_child_queryset():
            return qs

        # Use the all_objects manager to ensure censored comments are included
        if name == "parent__isnull" and value is True:
            from researchhub_comment.models import RhCommentModel

            # Get all IDs from the current queryset
            ids = qs.values_list("id", flat=True)
            # Return a queryset with all objects (including censored)
            # filtered by these IDs and parent=None
            return RhCommentModel.all_objects.filter(id__in=ids, parent__isnull=True)

        return qs.filter(**{name: value})
