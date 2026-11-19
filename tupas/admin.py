from django.contrib import admin
from django.db.models import Count

from .models import (
    ActivityLog,
    Deck,
    EnumerationItem,
    ExamAttempt,
    ItemProgress,
    QuizChoice,
    ReviewItem,
    StudentProfile,
    StudyChatMessage,
    StudyDocument,
    Subject,
)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        'full_name',
        'user',
        'role',
        'student_level',
        'school_name',
        'course_or_strand',
        'year_level',
        'city_municipality',
        'province',
        'region',
        'xp_points',
        'show_on_leaderboard',
    )
    list_filter = (
        'student_level',
        'role',
        'show_on_leaderboard',
        'region',
        'province',
        'city_municipality',
        'school_name',
        'year_level',
    )
    search_fields = (
        'full_name',
        'user__username',
        'user__email',
        'school_name',
        'course_or_strand',
    )
    autocomplete_fields = ('user',)
    list_select_related = ('user',)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('subject_name', 'target_course', 'deck_count')
    search_fields = ('subject_name', 'target_course', 'description')
    list_filter = ('target_course',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_deck_count=Count('decks'))

    @admin.display(description='Decks', ordering='_deck_count')
    def deck_count(self, obj):
        return obj._deck_count


class EnumerationItemInline(admin.TabularInline):
    model = EnumerationItem
    extra = 1
    fields = ('order_number', 'answer_text')
    ordering = ('order_number',)


class QuizChoiceInline(admin.TabularInline):
    model = QuizChoice
    extra = 1
    fields = ('choice_text', 'is_correct')
    ordering = ('id',)


@admin.register(ReviewItem)
class ReviewItemAdmin(admin.ModelAdmin):
    list_display = (
        'short_prompt',
        'deck',
        'item_type',
        'main_answer',
        'is_strict_order',
        'quality_flag',
        'choice_count',
        'enum_count',
    )
    list_filter = (
        'item_type',
        'is_strict_order',
        'quality_flag',
        'deck__subject',
        'deck',
    )
    search_fields = (
        'prompt_text',
        'main_answer',
        'explanation',
        'deck__title',
        'deck__subject__subject_name',
    )
    list_select_related = ('deck', 'deck__subject')
    inlines = (EnumerationItemInline, QuizChoiceInline)
    fieldsets = (
        (None, {
            'fields': ('deck', 'item_type', 'prompt_text'),
        }),
        ('Answers', {
            'fields': ('main_answer', 'explanation', 'is_strict_order'),
        }),
    )

    @admin.display(description='Prompt')
    def short_prompt(self, obj):
        return obj.prompt_text[:70] + ('…' if len(obj.prompt_text) > 70 else '')

    @admin.display(description='Choices')
    def choice_count(self, obj):
        return obj.choices.count()

    @admin.display(description='Enum answers')
    def enum_count(self, obj):
        return obj.enum_answers.count()


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'subject', 'item_count', 'created_by', 'created_at', 'is_pinned', 'is_archived', 'tags')
    list_filter = ('subject', 'created_at', 'is_pinned', 'is_archived')
    search_fields = ('title', 'description', 'subject__subject_name')
    autocomplete_fields = ('subject', 'created_by')
    list_select_related = ('subject', 'created_by')
    date_hierarchy = 'created_at'

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_item_count=Count('items'))

    @admin.display(description='Items', ordering='_item_count')
    def item_count(self, obj):
        return obj._item_count


@admin.register(EnumerationItem)
class EnumerationItemAdmin(admin.ModelAdmin):
    list_display = ('review_item', 'order_number', 'answer_text')
    list_filter = ('review_item__deck__subject', 'review_item__is_strict_order')
    search_fields = ('answer_text', 'review_item__prompt_text', 'review_item__deck__title')
    autocomplete_fields = ('review_item',)
    list_select_related = ('review_item', 'review_item__deck')


@admin.register(QuizChoice)
class QuizChoiceAdmin(admin.ModelAdmin):
    list_display = ('review_item', 'choice_text', 'is_correct')
    list_filter = ('is_correct', 'review_item__deck__subject')
    search_fields = ('choice_text', 'review_item__prompt_text', 'review_item__deck__title')
    autocomplete_fields = ('review_item',)
    list_select_related = ('review_item', 'review_item__deck')


@admin.register(ItemProgress)
class ItemProgressAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'review_item',
        'mastery_status',
        'last_reviewed',
    )
    list_filter = (
        'mastery_status',
        'last_reviewed',
        'review_item__deck__subject',
    )
    search_fields = (
        'user__username',
        'user__email',
        'review_item__prompt_text',
        'review_item__deck__title',
    )
    autocomplete_fields = ('user', 'review_item')
    list_select_related = ('user', 'review_item', 'review_item__deck')
    date_hierarchy = 'last_reviewed'


@admin.register(ExamAttempt)
class ExamAttemptAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'deck',
        'exam_mode',
        'score',
        'total_items',
        'percentage',
        'date_taken',
    )
    list_filter = (
        'exam_mode',
        'date_taken',
        'deck__subject',
        'deck',
    )
    search_fields = (
        'user__username',
        'user__email',
        'deck__title',
        'deck__subject__subject_name',
    )
    autocomplete_fields = ('user', 'deck')
    list_select_related = ('user', 'deck')
    date_hierarchy = 'date_taken'
    readonly_fields = ('date_taken',)


class StudyChatMessageInline(admin.TabularInline):
    model = StudyChatMessage
    extra = 0
    readonly_fields = ('sender', 'message', 'created_at')


@admin.register(StudyDocument)
class StudyDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'page_count', 'word_count_display', 'generated_deck', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('title', 'user__username', 'extracted_text')
    autocomplete_fields = ('user', 'generated_deck')
    inlines = [StudyChatMessageInline]

    def word_count_display(self, obj):
        return f"{obj.word_count:,} words"
    word_count_display.short_description = "Word Count"


@admin.register(StudyChatMessage)
class StudyChatMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'document', 'short_message', 'created_at')
    list_filter = ('sender', 'created_at')
    search_fields = ('message', 'document__title', 'document__user__username')

    def short_message(self, obj):
        return obj.message[:60]
    short_message.short_description = "Message"


admin.site.site_header = 'TUPAS Reviewer Administration'
admin.site.site_title = 'TUPAS Reviewer Admin'
admin.site.index_title = 'Content and student management'


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'username_snapshot', 'action', 'detail',
        'ip_address', 'request_id',
    )
    list_filter = ('action', 'created_at')
    search_fields = (
        'username_snapshot', 'user__username', 'detail', 'ip_address', 'request_id',
    )
    date_hierarchy = 'created_at'
    list_select_related = ('user',)
    readonly_fields = (
        'user', 'username_snapshot', 'user_id_snapshot', 'request_id',
        'action', 'detail', 'ip_address', 'user_agent', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        """Remove the bulk delete action so the trail cannot be erased."""
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

