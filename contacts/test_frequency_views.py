from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from . import test_frequency
from .views import body,error
from .mail_integration_views import private_response


@require_http_methods(['GET','POST'])
def control(request):
    try:
        if request.method=='POST':test_frequency.configure(body(request))
        return private_response(JsonResponse(test_frequency.report(request.GET if request.method=='GET' else {})))
    except ValueError as exc:return error(str(exc))
