from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from . import views

app_name = 'tupas'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path(
        'offline/',
        TemplateView.as_view(template_name='tupas/offline.html'),
        name='offline',
    ),
    path('welcome/', TemplateView.as_view(template_name='tupas/auth/landing.html'), name='landing'),
    path(
        'sw.js',
        never_cache(TemplateView.as_view(
            template_name='sw.js',
            content_type='application/javascript',
        )),
        name='service_worker',
    ),
    path('health/', views.health, name='health'),
    path('health', views.health),
    path('profile/', views.profile, name='profile'),
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('users/', views.user_list, name='user_list'),
    path('users/<int:user_id>/', views.user_detail, name='user_detail'),
    path('users/<int:user_id>/role/', views.set_user_role, name='set_user_role'),
    path('users/<int:user_id>/account/', views.admin_edit_account, name='admin_edit_account'),
    path('users/<int:user_id>/password/', views.admin_set_password, name='admin_set_password'),
    path('ai/generate/', views.ai_generator, name='ai_generator'),
    path('ai/extract-image/', views.extract_image, name='extract_image'),
    path('ai/extract-pptx/', views.extract_text_from_pptx_stream_view, name='extract_pptx'),
    path('study-buddy/', views.study_buddy, name='study_buddy'),
    path('study-buddy/<int:doc_id>/', views.study_buddy_detail, name='study_buddy_detail'),
    path(
        'study-buddy/<int:doc_id>/chat/',
        views.study_buddy_chat,
        name='study_buddy_chat',
    ),
    path(
        'study-buddy/<int:doc_id>/clear/',
        views.study_buddy_clear_chat,
        name='study_buddy_clear_chat',
    ),
    path(
        'study-buddy/<int:doc_id>/edit/',
        views.study_buddy_edit_doc,
        name='study_buddy_edit_doc',
    ),
    path(
        'study-buddy/<int:doc_id>/delete/',
        views.study_buddy_delete_doc,
        name='study_buddy_delete_doc',
    ),
    path(
        'study-buddy/<int:doc_id>/messages/<int:msg_id>/edit/',
        views.study_buddy_edit_message,
        name='study_buddy_edit_message',
    ),
    path(
        'study-buddy/<int:doc_id>/messages/<int:msg_id>/delete/',
        views.study_buddy_delete_message,
        name='study_buddy_delete_message',
    ),
    path(
        'study-buddy/<int:doc_id>/generate-deck/',
        views.study_buddy_generate_deck,
        name='study_buddy_generate_deck',
    ),
    path('register/', views.register, name='register'),
    path('login/', views.RateLimitedLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('decks/<int:deck_id>/', views.deck_detail, name='deck_detail'),
    path('decks/<int:deck_id>/cram-sheet/', views.deck_cram_sheet, name='deck_cram_sheet'),
    path('decks/<int:deck_id>/clone/', views.clone_deck, name='clone_deck'),
    path('decks/<int:deck_id>/merge/', views.merge_deck, name='merge_deck'),
    path('decks/<int:deck_id>/toggle-pin/', views.toggle_pin_deck, name='toggle_pin_deck'),
    path('decks/<int:deck_id>/toggle-archive/', views.toggle_archive_deck, name='toggle_archive_deck'),
    path('decks/<int:deck_id>/export/', views.export_deck, name='export_deck'),
    path('decks/<int:deck_id>/export/attempts/', views.export_attempts, name='export_attempts'),
    path('decks/import/', views.import_deck, name='import_deck'),
    path('exams/attempts/<int:attempt_id>/', views.exam_attempt_detail, name='exam_attempt_detail'),
    path('items/<int:item_id>/explain-deeper/', views.explain_deeper, name='explain_deeper'),
    path('items/<int:item_id>/feedback/', views.item_feedback, name='item_feedback'),
    path('items/<int:item_id>/regenerate/', views.regenerate_item, name='regenerate_item'),
    path('decks/<int:deck_id>/items/add/', views.add_review_item, name='add_review_item'),
    path(
        'decks/<int:deck_id>/items/<int:item_id>/edit/',
        views.edit_review_item,
        name='edit_review_item',
    ),
    path(
        'decks/<int:deck_id>/items/<int:item_id>/delete/',
        views.delete_review_item,
        name='delete_review_item',
    ),
    path('decks/<int:deck_id>/flip/', views.flip_mode, name='flip_mode'),
    path('decks/<int:deck_id>/highlight/', views.highlight_mode, name='highlight_mode'),
    path('decks/<int:deck_id>/enumeration/', views.enumeration_mode, name='enumeration_mode'),
    path('decks/<int:deck_id>/exam/<slug:mode>/', views.exam_mode, name='exam_mode'),
    path('api/progress/<int:item_id>/', views.mark_progress, name='mark_progress'),
]
