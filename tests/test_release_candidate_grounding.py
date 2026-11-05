import copy
import pytest
from app import reports

@pytest.mark.parametrize('source,quote', [('Альфа, готово.','Альфа готово.'),('Альфа готово.','альфа готово.'),('Альфа  готово.','Альфа готово.'),('Альфа — готово.','Альфа - готово.')])
def test_rewritten_quote_is_not_source(source,quote):
    assert reports.quote_source(quote,reports.lines([dict(start=0,end=1,text=source)])) is None

@pytest.mark.parametrize('texts',[['Повтор.','Повтор.'],['Повтор. Повтор.']])
def test_ambiguous_literal_is_not_bound_to_first_row(texts):
    items=reports.lines([dict(start=i,end=i+1,text=t) for i,t in enumerate(texts)])
    assert reports.quote_source('Повтор.',items) is None

def test_adjacent_literal_keeps_original_first_row():
    items=reports.lines([dict(start=3,end=4,text='Первая часть'),dict(start=4,end=5,text='и вторая часть.')])
    assert reports.quote_source('часть и вторая',items)['id']=='L00001'
    assert reports.quote_source('и вторая часть.',items)['id']=='L00002'

@pytest.mark.parametrize('field,evidence,expected',[('Анна','Жанна подготовит отчет.',False),('Анна','Анна подготовит отчет.',True),('Иван','Иванов подготовит отчет.',False),('к пятнице','Подготовить к пятнице.',True),('к пятнице','Подготовить к следующей пятнице.',False)])
def test_field_requires_literal_whole_phrase(field,evidence,expected):
    assert reports.source_field_present(field,evidence) is expected

def test_prompt_has_no_conflicting_date_or_identity_instruction():
    meta=dict(meeting_title='Учебная',meeting_date='не указана',participants='не указаны',glossary='нет')
    system,user=reports.prompt('03_extract_facts.md',meta,chunk_index=1,chunk_total=1,previous_context='',chunk='L00001 [00:00:01] Неопознанный говорящий: Проверить.')
    assert system.endswith(reports.OVERRIDES)
    assert 'due_date всегда null' in system
    assert 'переводи в дату относительно даты встречи' not in system
    assert 'Имена пиши как в списке участников' not in system
    assert 'Порядковый номер задачи' in system

def test_missing_citation_cannot_be_approved_by_model():
    facts={k:'Сводка' if k=='summary' else [] for k in reports.schema('facts')['properties']}
    facts['action_items']=[dict(task='Подготовить',evidence='Выдуманная цитата',confidence='low',assignee=None)]
    class Approve:
        def chat(self,*args):return dict(verdict='подтверждено',reason='Учебный ответ',evidence='Выдуманная цитата')
    result=reports.verify_facts(copy.deepcopy(facts),reports.lines([dict(start=0,end=1,text='Исходный текст.')]),Approve(),{})
    assert result['action_items']==[] and len(result['rejected_items'])==1
