from enum import Enum, auto


class AccountStatus(Enum):
    IDLE      = auto()   # вільний, чекає на задачі
    WORKING   = auto()   # виконує задачу
    COOLDOWN  = auto()   # навмисна пауза
    ERROR     = auto()   # проблема, бот намагається відновитись
    DEAD      = auto()   # не відновився — потрібне зовнішнє втручання
    SUSPENDED = auto()   # призупинений через спрацювання guard-умов