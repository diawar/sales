from django.http import JsonResponse
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from .models import AutomationScenario,AutomationSimulationRun
from . import automation_simulation as engine
from .views import body,error


@require_http_methods(['GET','POST'])
def scenarios(request,scenario_id=None):
    if request.method=='GET':
        if scenario_id is not None:
            row=AutomationScenario.objects.filter(pk=scenario_id).first()
            return JsonResponse(engine.scenario_record(row)) if row else error('Сценарий не найден.',404)
        return JsonResponse({'mode':'simulation','can_send':False,
            'scenarios':[engine.scenario_record(r) for r in AutomationScenario.objects.order_by('-updated_at')[:200]]})
    try:
        row=engine.save(body(request),scenario_id)
        return JsonResponse(engine.scenario_record(row),status=200 if scenario_id else 201)
    except ValueError as exc:return error(str(exc))


@require_POST
def run(request,scenario_id):
    try:
        data=body(request);engine.simulation_only(data)
        value=engine.start(scenario_id,data.get('revision'))
        return JsonResponse({'run_id':value.pk,'mode':'simulation','can_send':False},status=201)
    except AutomationScenario.DoesNotExist:return error('Сценарий не найден.',404)
    except ValueError as exc:return error(str(exc))


@require_http_methods(['GET','POST'])
def simulation(request):
    if request.method=='GET':
        try:return JsonResponse(engine.report(request.GET))
        except ValueError as exc:return error(str(exc))
    try:
        data=body(request);engine.simulation_only(data)
        action=data.get('action')
        if action in ('tick','advance'):
            minutes=data.get('minutes',0) if action=='advance' else 0
            performed=engine.tick(minutes)
            return JsonResponse({'mode':'simulation','can_send':False,'simulated':performed})
        run_id=data.get('run_id')
        if type(run_id) is not int or not 1<=run_id<=2147483647:raise ValueError('Выберите имитацию.')
        value=engine.control(run_id,action)
        return JsonResponse({'run_id':value.pk,'state':value.state,'can_send':False})
    except AutomationSimulationRun.DoesNotExist:return error('Имитация не найдена.',404)
    except ValueError as exc:return error(str(exc))
