from typing import Dict, NewType

# MatrixId = NewType('MatrixId', str)
MatrixUserId = NewType('MatrixUserId', str)
MatrixRoomId = NewType('MatrixRoomId', str)
MatrixEventId = NewType('MatrixEventId', str)

MatrixEvent = NewType('MatrixEvent', Dict)

TelegramId = NewType('TelegramId', int)
