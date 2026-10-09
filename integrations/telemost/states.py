"""Stable public lifecycle states. Reasons describe observed evidence, not inferred causes."""
from enum import Enum


class Status(str, Enum):
    STARTING='starting'
    OPENING='opening'
    PREPARING='preparing'
    WAITING_HOST='waiting_host'
    JOINED='joined'
    RECORDING='recording'
    STOPPING='stopping'
    FINISHING='finishing'
    SAVED='saved'
    UPLOADING='uploading'
    UPLOADED='uploaded'
    PARTIAL_SAVED='partial_saved'
    UPLOAD_UNCERTAIN='upload_uncertain'
    FAILED='failed'
    CANCELLED='cancelled'


TERMINAL={Status.UPLOADED.value, Status.PARTIAL_SAVED.value, Status.UPLOAD_UNCERTAIN.value,
          Status.FAILED.value, Status.CANCELLED.value}

# Observed Russian web UI, verified 2026-10-08. Kept together for later selector updates.
CONTROLS={'more':'Ещё', 'leave':'Выйти из встречи', 'participants':'Участники',
          'start_recording':'Записать на компьютер', 'stop_recording':'Остановить запись на компьютер'}
