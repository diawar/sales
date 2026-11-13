from django.urls import path
from contacts import views
from contacts import collection_views
from contacts import profile_views
from contacts import producer_views
from contacts import journal_views
from contacts import action_audit_views
from contacts import gov_views
from contacts import dfo_views
from contacts import email_queue_views
from contacts import segment_views
from contacts import overshop_views
from contacts import kaspi_views
from contacts import template_views
from contacts import template_mail
from contacts import accounts
from contacts import notification_views
urlpatterns=[path('',views.static_file),path('api/contacts',views.contacts),path('api/audiences',views.audiences),path('api/contacts/<int:contact_id>/notes',views.note),path('api/import/preview',views.preview),path('api/import/commit',views.commit),path('<str:filename>',views.static_file)]
urlpatterns.insert(1,path('api/collection',collection_views.status))
urlpatterns += [path('api/notifications',notification_views.center)]
urlpatterns += [path('login/',accounts.sign_in,name='login'),path('setup/',accounts.setup,name='account-setup'),
    path('logout/',accounts.sign_out),path('accounts/',accounts.accounts),path('api/auth/session',accounts.session),path('api/accounts',accounts.account_list)]
urlpatterns += [path('api/clients',profile_views.save),path('api/contacts/<int:contact_id>/profile',profile_views.save)]
urlpatterns += [path('api/producers',producer_views.status)]
urlpatterns += [path('api/journal',journal_views.journal)]
urlpatterns += [path('api/action-journal',action_audit_views.journal),path('api/action-journal/operator',action_audit_views.operator)]
urlpatterns += [path('api/gov',gov_views.status)]
urlpatterns += [path('api/contacts/<int:contact_id>/detail',profile_views.detail)]
urlpatterns += [path('api/contacts/page',views.contacts_page)]
urlpatterns += [path('api/contacts/summary',views.contacts_summary),path('api/contacts/export',views.contacts_export)]
urlpatterns += [path('api/dfo',dfo_views.status)]
urlpatterns += [path('api/email-check',email_queue_views.status)]
urlpatterns += [path('api/segments',segment_views.segments)]
urlpatterns += [path('api/overshop/preview',overshop_views.preview)]
urlpatterns += [path('api/overshop/subscribers',overshop_views.subscribers)]
urlpatterns += [path('api/kaspi',kaspi_views.status)]
urlpatterns += [path('api/templates',template_views.templates),path('api/templates/<int:template_id>',template_views.templates)]
urlpatterns += [path('api/templates/check',template_views.check),path('api/templates/ai',template_views.ai)]
urlpatterns += [path('api/templates/test-mail',template_mail.test_mail)]

from contacts import email_policy_views
urlpatterns += [path('api/contacts/<int:contact_id>/email-status',email_policy_views.address_status)]

from contacts import campaign_views
from contacts import campaign_approval
urlpatterns += [path('api/campaigns/<int:campaign_id>/approval',campaign_approval.approval)]
urlpatterns += [path("api/campaigns",campaign_views.campaigns), path("api/campaigns/<int:campaign_id>",campaign_views.campaigns), path("api/campaigns/preview",campaign_views.preview), path("api/campaigns/recipients",campaign_views.recipient_preview), path("api/campaign-senders",campaign_views.senders)]

from contacts import dispatch_queue_views
urlpatterns += [path("api/campaigns/<int:campaign_id>/queue",dispatch_queue_views.queue)]

from contacts import campaign_statistics_views
urlpatterns += [path("api/campaign-stats",campaign_statistics_views.report),path("api/campaign-stats/test-events",campaign_statistics_views.test_event),path("api/campaign-stats/demo",campaign_statistics_views.generate_demo)]

from contacts import campaign_readiness_views
urlpatterns += [path("api/campaigns/<int:campaign_id>/readiness",campaign_readiness_views.readiness)]

from contacts import subscription_views
urlpatterns += [path("api/subscriptions",subscription_views.subscriptions),path("api/subscriptions/<int:subscription_id>",subscription_views.detail),path("api/subscriptions/<int:subscription_id>/unsubscribe-link",subscription_views.create_unsubscribe_link),path("subscription/<str:token>/",subscription_views.public,name="subscription-public")]

from contacts import activity_audience_views
urlpatterns += [path("api/activity-audiences",activity_audience_views.audiences),path("api/activity-audiences/demo",activity_audience_views.demo),path("api/activity-audiences/test-events",activity_audience_views.test_event)]

from contacts import mail_integration_views
from contacts import campaign_mail_check
from contacts import automation_views
from contacts import rehearsal_views
from contacts import test_frequency_views
urlpatterns += [path('api/test-frequency',test_frequency_views.control)]
urlpatterns += [path('api/rehearsals',rehearsal_views.sessions),
    path('api/rehearsals/<int:rehearsal_id>',rehearsal_views.sessions),
    path('api/rehearsals/<int:rehearsal_id>/letters/<int:letter_id>',rehearsal_views.letter)]
urlpatterns += [path('api/automations',automation_views.scenarios),
    path('api/automations/<int:scenario_id>',automation_views.scenarios),
    path('api/automations/<int:scenario_id>/run',automation_views.run),
    path('api/automation-simulation',automation_views.simulation)]
urlpatterns += [path('api/campaigns/<int:campaign_id>/mail-check',campaign_mail_check.mail_check)]
urlpatterns += [path('api/servercore',mail_integration_views.connection),
    path('api/mail-tracking/preview',mail_integration_views.preview),
    path('api/mail-tracking',mail_integration_views.report),
    path('track/o/<str:token>.gif',mail_integration_views.pixel,name='tracking-open'),
    path('track/c/<str:token>/',mail_integration_views.click,name='tracking-click')]
