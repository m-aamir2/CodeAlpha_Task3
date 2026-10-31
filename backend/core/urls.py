from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import AvatarView, ChangePasswordView, FileDownloadView, ForgotPasswordView, HealthView, InviteView, LiveKitTokenView, LoginView, LogoutView, MeView, MeetingDetailView, MeetingEndView, MeetingFileListCreateView, MeetingJoinStatusView, MeetingJoinView, MeetingLeaveView, MeetingListCreateView, MeetingLookupView, MessageListCreateView, NotificationListView, ParticipantListView, RegisterView, ResetPasswordView, StatisticsView, WhiteboardView

urlpatterns = [
    path("health/", HealthView.as_view()),
    path("auth/register/", RegisterView.as_view()), path("auth/login/", LoginView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()), path("auth/logout/", LogoutView.as_view()),
    path("auth/forgot-password/", ForgotPasswordView.as_view()), path("auth/reset-password/", ResetPasswordView.as_view()),
    path("auth/change-password/", ChangePasswordView.as_view()), path("auth/me/", MeView.as_view()),
    path("avatars/<int:user_id>/", AvatarView.as_view()),
    path("meetings/", MeetingListCreateView.as_view()), path("meetings/lookup/<str:code>/", MeetingLookupView.as_view()),
    path("meetings/<str:pk>/", MeetingDetailView.as_view()), path("meetings/<uuid:pk>/join/status/", MeetingJoinStatusView.as_view()),
    path("meetings/<str:pk>/join/", MeetingJoinView.as_view()), path("meetings/<uuid:pk>/leave/", MeetingLeaveView.as_view()),
    path("meetings/<uuid:pk>/end/", MeetingEndView.as_view()), path("meetings/<uuid:pk>/participants/", ParticipantListView.as_view()),
    path("meetings/<uuid:pk>/invite/", InviteView.as_view()), path("meetings/<uuid:pk>/token/", LiveKitTokenView.as_view()),
    path("meetings/<uuid:pk>/messages/", MessageListCreateView.as_view()), path("meetings/<uuid:pk>/files/", MeetingFileListCreateView.as_view()),
    path("meetings/<uuid:pk>/whiteboard/", WhiteboardView.as_view()), path("files/<int:file_id>/", FileDownloadView.as_view()),
    path("notifications/", NotificationListView.as_view()), path("notifications/<int:pk>/read/", NotificationListView.as_view()),
    path("notifications/read-all/", NotificationListView.as_view()), path("stats/", StatisticsView.as_view()),
]

