from ..core.service import register_service
from .players import *
from .players.service import PlayerService
from .teams import *
from .teams.service import TeamService

register_service(PlayerService)
register_service(TeamService)
