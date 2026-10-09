"""Observed Telemost identity and timing; active-tile hints are not voice identification."""
from typing import Literal
from pydantic import BaseModel,Field,field_validator
from app.db import MeetingContext

class Participant(BaseModel):
    name:str=Field(min_length=1,max_length=200)
    role:Literal['participant','moderator','organizer','presenter']='participant'
class Turn(BaseModel):
    start:float=Field(ge=0,le=8000,allow_inf_nan=False)
    end:float=Field(ge=0,le=8000,allow_inf_nan=False)
    names:list[str]=Field(max_length=100)
    @field_validator('names')
    @classmethod
    def names_valid(cls,names):
        if any(not n or len(n)>200 for n in names):raise ValueError('name')
        return list(dict.fromkeys(names))
class Context(BaseModel):
    source:Literal['telemost_ui']='telemost_ui'
    timing_reliable:bool=True
    title:str|None=Field(default=None,max_length=200)
    participants:list[Participant]=Field(default_factory=list,max_length=100)
    turns:list[Turn]=Field(default_factory=list,max_length=20000)
    @field_validator('turns')
    @classmethod
    def chronological(cls,turns):
        last=0
        for t in turns:
            if t.end<t.start or t.start<last:raise ValueError('turn_order')
            last=t.end
        return turns

def data(s,mid):
    row=s.get(MeetingContext,mid)
    return row.observations if row else {}

def annotate(segments,context):
    turns=context.get('turns',[]) if context.get('timing_reliable',True) else [];known={p['name'] for p in context.get('participants',[])}
    result=[]
    for item in segments or []:
        segment=dict(item);start=float(item['start']);end=float(item['end']);duration=end-start
        coverage={};ambiguous=0
        for turn in turns:
            if turn['start']>=end:break
            overlap=max(0,min(end,turn['end'])-max(start,turn['start']))
            if overlap<=0:continue
            names=turn['names']
            if len(names)==1 and names[0] in known:coverage[names[0]]=coverage.get(names[0],0)+overlap
            else:ambiguous+=overlap
        if duration>0 and coverage:
            name,seconds=max(coverage.items(),key=lambda x:x[1])
            if seconds/duration>=0.9 and ambiguous/duration<=0.05:
                segment['speaker_hint']=name;segment['speaker_hint_source']='telemost_ui_active_tile'
        result.append(segment)
    return result

ROLE_NAMES={'participant':'участник','moderator':'модератор','organizer':'организатор','presenter':'демонстрирует экран'}
def roster(context):
    return '\n'.join(f'{p["name"]}: {ROLE_NAMES[p["role"]]} (роль в Телемосте; должность не установлена)' for p in context.get('participants',[]))
