import sys
import os
os.system('chmod +x engines/sf-12')

from api import API
from challenge_handler import Challenge_Handler
from config import load_config
from enums import Challenge_Color, Variant
from game_counter import Game_Counter
from logo import LOGO
from matchmaking import Matchmaking
from matchmaking2 import Matchmaking2

COMMANDS = {'abort': 'Aborts a game. Usage: abort GAME_ID',
            'challenge': 'Challenges a player. Usage: challenge USERNAME [INITIAL_TIME] [INCREMENT] [COLOR] [RATED]',
            'help': 'Prints this message.', 'matchmaking': 'Starts matchmaking mode. Usage: matchmaking [VARIANT]',
            'quit': 'Exits the bot.', 'reset': 'Resets matchmaking.', 'stop': 'Stops matchmaking mode.',
            'upgrade': 'Upgrades your Lichess account to a BOT account.', 'matchmaking2': 'New matchmaking'}


class UserInterface:
    def __init__(self) -> None:
        self.config = load_config()
        self.api = API(self.config['token'])
        self.game_count = Game_Counter(self.config['challenge'].get('concurrency', 1))
        self.is_running = True
        self.matchmaking: Matchmaking | None = None

    def start(self) -> None:
        print(LOGO)

        self.challenge_handler = Challenge_Handler(self.config, self.api, self.game_count)
        self.challenge_handler.start()

        try:
            import readline

            completer = Autocompleter(list(COMMANDS.keys()))
            readline.set_completer(completer.complete)
            readline.parse_and_bind('tab: complete')
        except ImportError:
            pass

        print('accepting challenges ...')

        while self.is_running:
            command = 'matchmaking2'
            
           

    def _start_matchmaking(self, variant: Variant) -> None:
        if self.matchmaking:
            return

        self.challenge_handler.stop_accepting_challenges()

        print('Waiting for a game to finish ...')
        self.game_count.wait_for_increment()

        print('Starting matchmaking ...')

        self.matchmaking = Matchmaking(self.config, self.api, variant)
        self.matchmaking.start()

    def _start_matchmaking2(self, variant: Variant) -> None:
        if self.matchmaking:
            return

        self.challenge_handler.stop_accepting_challenges()

        print('Waiting for a game to finish ...')
        self.game_count.wait_for_increment()

        print('Starting matchmaking ...')

        self.matchmaking = Matchmaking2(self.config, self.api, variant)
        self.matchmaking.start()


class Autocompleter:
    def __init__(self, options: list[str]) -> None:
        self.options = options

    def complete(self, text: str, state: int) -> str:
        if state == 0:
            if text:
                self.matches = [s for s in self.options if s and s.startswith(text)]
            else:
                self.matches = self.options[:]

        try:
            return self.matches[state]
        except IndexError:
            return None


if __name__ == '__main__':

    ui = UserInterface()
    ui.start()
