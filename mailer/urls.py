from django.urls import path
from contacts import views
from contacts import collection_views
from contacts import profile_views
from contacts import producer_views
from contacts import journal_views
from contacts import gov_views
from contacts import dfo_views
from contacts import email_queue_views
from contacts import segment_views
from contacts import overshop_views
from contacts import kaspi_views
urlpatterns=[path('',views.static_file),path('api/contacts',views.contacts),path('api/audiences',views.audiences),path('api/contacts/<int:contact_id>/notes',views.note),path('api/import/preview',views.preview),path('api/import/commit',views.commit),path('<str:filename>',views.static_file)]
urlpatterns.insert(1,path('api/collection',collection_views.status))
urlpatterns += [path('api/clients',profile_views.save),path('api/contacts/<int:contact_id>/profile',profile_views.save)]
urlpatterns += [path('api/producers',producer_views.status)]
urlpatterns += [path('api/journal',journal_views.journal)]
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
