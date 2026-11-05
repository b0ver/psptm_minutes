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
            return {'verdict': self.verdict, 'reason_code': 'state_supported' if self.verdict == 'confirmed' else 'state_contradicted',
                    'support': json.loads(user)['classification']['support']}
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
        assert set(user['candidate']) == {'action_ref', 'task', 'original_quote', 'original_quote_source_spans'}
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


def test_adjacent_original_quote_preserves_exact_source_spans_in_both_requests():
    source = reports.lines([{'start': 0, 'end': 1, 'text': 'Передать результат.'},
                            {'start': 2, 'end': 3, 'text': 'Беру эту работу.'}])
    action = {**ACTION, 'evidence': 'Передать результат. Беру эту работу.'}
    model = Model('active', [{'source_id': 'L00002', 'quote': 'Беру эту работу.'}])
    active, _, _ = run(model, source=source, action=action)
    assert active == [action]
    for _, request, _ in model.calls:
        spans = request['candidate']['original_quote_source_spans']
        assert len(spans) == 2 and ' '.join(x['quote'] for x in spans) == action['evidence']
        for span, row in zip(spans, source):
            assert span['source_id'] == row['id']
            assert row['text'][span['start_char']:span['end_char']] == span['quote']


@pytest.mark.parametrize('quote', ['Отчет. Отчет.', 'Отчет!'])
def test_unbound_or_ambiguous_original_quote_fails_before_model_call(quote):
    model = Model()
    source = reports.lines([{'start': i, 'end': i + 1, 'text': 'Отчет.'} for i in range(3)])
    with pytest.raises(ProcessingError) as error:
        run(model, source=source, action={**ACTION, 'evidence': quote})
    assert error.value.code == 'analysis_action_status_original_quote_ungrounded'
    assert not model.calls


@pytest.mark.parametrize('support,reason', [([], 'state_supported'),
    ([{'source_id': 'L00002', 'quote': 'Не существующая реплика'}], 'state_supported'),
    ([{'source_id': 'L00002', 'quote': 'Отменяем подготовку отчета.'}], 'state_contradicted')])
def test_confirmed_without_literal_positive_review_cannot_pass(support, reason):
    class Unsupported(Model):
        def chat(self, system, user, spec):
            if spec == CHECK_SCHEMA:
                return {'verdict': 'confirmed', 'reason_code': reason, 'support': support}
            return super().chat(system, user, spec)
    with pytest.raises(ProcessingError):
        run(Unsupported())
