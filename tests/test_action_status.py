import copy
import json
import pytest
from app.action_status import classify_actions, CLASSIFY_SCHEMA, CHECK_SCHEMA
from app.ai import ProcessingError
from app import reports

SOURCE = reports.lines([
    {'start': 1, 'end': 2, 'text': 'Подготовить отчет.'},
    {'start': 3, 'end': 4, 'text': 'Отменяем подготовку отчета.'},
    {'start': 5, 'end': 6, 'text': 'Возобновляем подготовку отчета.'},
])
ACTION = {'task': 'Подготовить отчет', 'evidence': 'Подготовить отчет.',
          'kind': 'назначено', 'note': 'Недоверенная заметка', 'assignee': None}


class Model:
    def __init__(self, state='cancelled', support=None, verdict='confirmed'):
        self.state, self.support, self.verdict = state, support, verdict
        self.calls = []

    def chat(self, system, user, spec):
        self.calls.append((system, json.loads(user), spec))
        if spec == CHECK_SCHEMA:
            return {'verdict': self.verdict}
        assert spec == CLASSIFY_SCHEMA
        return {'state': self.state, 'support': self.support if self.support is not None else [
            {'source_id': 'L00002', 'quote': 'Отменяем подготовку отчета.'}]}


def run(model, source=SOURCE, action=ACTION, budget=45000):
    return classify_actions([copy.deepcopy(action)], source, model, reports.validate_json, budget)


def test_cancellation_is_context_not_proposed_or_active_action():
    model = Model()
    active, context, audit = run(model, action={**ACTION, 'kind': 'предложено'})
    assert not active and context[0]['activity_state'] == 'cancelled'
    assert audit[0]['opposed_verdict'] == 'confirmed' and len(model.calls) == 2
    for _, user, _ in model.calls:
        assert len(user['source']) == 3
        assert set(user['candidate']) == {'action_ref', 'task', 'original_quote'}
        assert 'note' not in user['candidate'] and 'report' not in user


@pytest.mark.parametrize('state', ['proposed', 'completed', 'historical'])
def test_other_non_active_states_remain_context(state):
    active, context, _ = run(Model(state))
    assert active == [] and context[0]['activity_state'] == state


def test_reactivated_action_survives_prior_cancellation():
    active, context, _ = run(Model('active', [{'source_id': 'L00003', 'quote': 'Возобновляем подготовку отчета.'}]))
    assert active == [ACTION] and not context


def test_no_keyword_based_cancellation_filter():
    active, _, _ = run(Model('active', [{'source_id': 'L00001', 'quote': 'Подготовить отчет.'}]))
    assert active == [ACTION]


@pytest.mark.parametrize('state,verdict,code', [('uncertain', 'confirmed', 'analysis_action_status_uncertain'),
    ('cancelled', 'rejected', 'analysis_action_status_not_confirmed'),
    ('cancelled', 'uncertain', 'analysis_action_status_not_confirmed')])
def test_uncertain_or_opposed_state_is_not_accepted(state, verdict, code):
    with pytest.raises(ProcessingError) as error:
        run(Model(state, verdict=verdict))
    assert error.value.code == code


@pytest.mark.parametrize('support', [[], [{'source_id': 'A00001', 'quote': 'Отменяем подготовку отчета.'}],
    [{'source_id': 'L00002', 'quote': 'Выдуманная отмена'}],
    [{'source_id': 'L00002', 'quote': ' '}]])
def test_status_requires_real_source_id_and_literal_quote(support):
    with pytest.raises(ProcessingError) as error:
        run(Model(support=support))
    assert error.value.code == 'analysis_action_status_ungrounded'


def test_late_cancellation_is_not_clipped_to_fit_context():
    model = Model()
    source = reports.lines([{'start': 0, 'end': 1, 'text': 'x' * 46000},
                            {'start': 2, 'end': 3, 'text': 'Отменяем подготовку отчета.'}])
    with pytest.raises(ProcessingError) as error:
        run(model, source)
    assert error.value.code == 'analysis_action_status_context_too_large' and not model.calls


def test_active_verdict_cannot_silently_promote_original_proposal_kind():
    with pytest.raises(ProcessingError) as error:
        run(Model('active'), action={**ACTION, 'kind': 'предложено'})
    assert error.value.code == 'analysis_action_status_kind_conflict'


def test_non_active_context_has_separate_bounded_render_channel():
    facts = {k: 'Сводка' if k == 'summary' else [] for k in reports.schema('facts')['properties']}
    facts['action_items'] = [copy.deepcopy(ACTION)]
    result = reports.verify_facts(facts, SOURCE, Model(), {})
    assert not result['action_items'] and result['action_context'][0]['activity_state'] == 'cancelled'
    _, user = reports.render_prompt('action_items', result, {})
    assert 'action_context' in user and 'cancelled' in user
