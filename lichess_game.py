import pickle
from typing import Tuple

import chess
import chess.engine
import chess.polyglot
from chess.variant import find_variant

from api import API
from enums import Variant

UCI_Move = str
CP_Score = int
Depth = int
Outcome = str
Offer_Draw = bool
Resign = bool


class Lichess_Game:
    def __init__(self, api: API, gameFull_event: dict, config: dict, username: str):
        self.config = config
        self.api = api
        self.board = self._setup_board(gameFull_event)
        self.username = username
        self.is_white: bool = gameFull_event['white'].get('name') == username
        self.initial_time: int = gameFull_event['clock']['initial']
        self.increment: int = gameFull_event['clock']['increment']
        self.white_time: int = gameFull_event['state']['wtime']
        self.black_time: int = gameFull_event['state']['btime']
        self.variant = Variant(gameFull_event['variant']['key'])
        self.draw_enabled: bool = config['engine']['offer_draw']['enabled']
        self.resign_enabled: bool = config['engine']['resign']['enabled']
        self.move_overhead = self._get_move_overhead()
        self.out_of_polyglot_counter = 0
        self.out_of_pybook_counter = 0
        self.out_of_cloud_counter = 0
        self.out_of_chessdb_counter = 0
        self.pybook_loaded = False
        self.engine = self._get_engine()
        self.scores: list[chess.engine.PovScore] = []

    def make_move(self) -> Tuple[UCI_Move, Offer_Draw, Resign]:
        if uci_move := self._make_pybook_move():
            move = chess.Move.from_uci(uci_move)
            message = f'PyBook:  {self._format_move(move):14}'
            offer_draw = False
            resign = False
        elif move := self._make_polyglot_move():
            message = f'Book:    {self._format_move(move):14}'
            offer_draw = False
            resign = False
        elif response := self._make_cloud_move():
            uci_move, cp_score, depth = response
            move = chess.Move.from_uci(uci_move)
            pov_score = chess.engine.PovScore(chess.engine.Cp(cp_score), chess.WHITE)
            message = f'Cloud:   {self._format_move(move):14} {self._format_score(pov_score)}     {depth}'
            offer_draw = False
            resign = False
        elif response := self._make_chessdb_move():
            uci_move, cp_score, depth = response
            move = chess.Move.from_uci(uci_move)
            pov_score = chess.engine.PovScore(chess.engine.Cp(cp_score), self.board.turn)
            message = f'ChessDB: {self._format_move(move):14} {self._format_score(pov_score)}     {depth}'
            offer_draw = False
            resign = False
        elif response := self._make_egtb_move():
            uci_move, outcome, offer_draw, resign = response
            offer_draw = offer_draw and self.draw_enabled
            resign = resign and self.resign_enabled
            move = chess.Move.from_uci(uci_move)
            message = f'EGTB:    {self._format_move(move):14} {outcome}'
        else:
            move, info = self._make_engine_move()
            message = f'Engine:  {self._format_move(move):14} {self._format_info(info)}'
            offer_draw = self._is_drawish()
            resign = self._is_resignable()

        print(message)
        self.last_message = message
        self.board.push(move)
        return move.uci(), offer_draw, resign

    def update(self, gameState_event: dict) -> bool:
        moves = gameState_event['moves'].split()
        if len(moves) <= len(self.board.move_stack):
            return False

        self.board.push(chess.Move.from_uci(moves[-1]))
        self.white_time = gameState_event['wtime']
        self.black_time = gameState_event['btime']

        return True

    def is_our_turn(self) -> bool:
        return self.is_white == self.board.turn

    def is_game_over(self) -> bool:
        return self.board.is_checkmate() or \
            self.board.is_stalemate() or \
            self.board.is_insufficient_material() or \
            self.board.is_fifty_moves() or \
            self.board.is_repetition()

    def is_abortable(self) -> bool:
        return self.board.ply() < 2

    def quit_engine(self):
        self.engine.quit()

    def _is_drawish(self) -> bool:
        if not self.draw_enabled:
            return False

        min_game_length = self.config['engine']['offer_draw']['min_game_length']
        consecutive_moves = self.config['engine']['offer_draw']['consecutive_moves']

        if self.board.fullmove_number < min_game_length or len(self.scores) < consecutive_moves:
            return False

        max_score = self.config['engine']['offer_draw']['score']

        for score in self.scores[-consecutive_moves:]:
            if abs(score.relative.score(mate_score=40000)) > max_score:
                return False

        return True

    def _is_resignable(self) -> bool:
        if not self.resign_enabled:
            return False

        consecutive_moves = self.config['engine']['resign']['consecutive_moves']

        if len(self.scores) < consecutive_moves:
            return False

        max_score = self.config['engine']['resign']['score']

        for score in self.scores[-consecutive_moves:]:
            if score.relative.score(mate_score=40000) > max_score:
                return False

        return True

    def _make_polyglot_move(self) -> chess.Move:
        enabled = self.config['engine']['polyglot']['enabled']
        selection = self.config['engine']['polyglot']['selection']
        out_of_book = self.out_of_polyglot_counter >= 10

        if not enabled or out_of_book:
            return

        for book in self._get_books():
            with chess.polyglot.open_reader(book) as book_reader:
                try:
                    if selection == 'weighted_random':
                        entry = book_reader.weighted_choice(self.board)
                    elif selection == 'uniform_random':
                        entry = book_reader.choice(self.board)
                    else:
                        entry = book_reader.find(self.board)
                except IndexError:
                    continue

                self.out_of_polyglot_counter = 0
                new_board = self.board.copy()
                new_board.push(entry.move)
                if not new_board.is_repetition(count=2):
                    return entry.move

        self.out_of_polyglot_counter += 1

    def _get_books(self) -> list[str]:
        books = self.config['engine']['polyglot']['books']

        if self.board.chess960 and 'chess960' in books:
            return books['chess960']
        else:
            if self.is_white and 'white' in books:
                return books['white']
            elif not self.is_white and 'black' in books:
                return books['black']

        return books['standard'] if 'standard' in books else []

    def _make_pybook_move(self) -> UCI_Move:
        enabled = self.config['engine']['pybook']['enabled']
        out_of_book = self.out_of_pybook_counter >= 10

        if not enabled or out_of_book:
            return

        if not self.pybook_loaded:
            with open(self.config['engine']['pybook']['book'], 'rb') as input:
                self.pybook: dict[int, str] = pickle.load(input)
            self.pybook_loaded = True

        if uci_move := self.pybook.get(chess.polyglot.zobrist_hash(self.board)):
            self.out_of_pybook_counter = 0
            new_board = self.board.copy()
            new_board.push(chess.Move.from_uci(uci_move))
            if not new_board.is_repetition(count=2):
                return uci_move
        else:
            self.out_of_pybook_counter += 1

    def _make_cloud_move(self) -> Tuple[UCI_Move, CP_Score, Depth]:
        enabled = self.config['engine']['online_moves']['lichess_cloud']['enabled']
        out_of_book = self.out_of_cloud_counter >= 10

        if not enabled or out_of_book:
            return

        has_time = self._has_time(self.config['engine']['online_moves']['lichess_cloud']['min_time'])
        timeout = self.config['engine']['online_moves']['lichess_cloud']['timeout']
        min_depth = self.config['engine']['online_moves']['lichess_cloud']['min_depth']

        if has_time:
            if response := self.api.get_cloud_eval(self.board.fen(), self.variant, timeout):
                if not 'error' in response:
                    if response["depth"] >= min_depth:
                        self.out_of_cloud_counter = 0
                        return response['pvs'][0]['moves'].split()[0], response["pvs"][0]["cp"], response["depth"]

                self.out_of_cloud_counter += 1
            else:
                self._reduce_own_time(timeout * 1000)

    def _make_chessdb_move(self) -> Tuple[UCI_Move, CP_Score, Depth]:
        enabled = self.config['engine']['online_moves']['chessdb']['enabled']
        out_of_book = self.out_of_chessdb_counter >= 10

        if not enabled or out_of_book:
            return

        has_time = self._has_time(self.config['engine']['online_moves']['chessdb']['min_time'])
        timeout = self.config['engine']['online_moves']['chessdb']['timeout']
        min_depth = self.config['engine']['online_moves']['chessdb']['min_depth']

        if has_time:
            if response := self.api.get_chessdb_eval(self.board.fen(), timeout):
                if response['status'] == 'ok':
                    if response["depth"] >= min_depth:
                        self.out_of_chessdb_counter = 0
                        return response["pv"][0], response["score"], response["depth"]

                self.out_of_chessdb_counter += 1
            else:
                self._reduce_own_time(timeout * 1000)

    def _make_egtb_move(self) -> Tuple[UCI_Move, Outcome, Offer_Draw, Resign]:
        if not self.config['engine']['online_moves']['online_egtb']['enabled']:
            return

        is_endgame = chess.popcount(self.board.occupied) <= 7
        has_time = self._has_time(self.config['engine']['online_moves']['online_egtb']['min_time'])
        timeout = self.config['engine']['online_moves']['online_egtb']['timeout']

        if is_endgame and has_time:
            if response := self.api.get_egtb(self.board.fen(), timeout):
                uci_move = response['moves'][0]['uci']
                outcome = response['category']
                return uci_move, outcome, outcome == 'draw', outcome == 'loss'
            else:
                self._reduce_own_time(timeout * 1000)

    def _make_engine_move(self) -> Tuple[chess.Move, chess.engine.InfoDict]:
        if self.board.ply() < 2:
            limit = chess.engine.Limit(time=10)
            ponder = False
        else:
            if self.is_white:
                white_time = self.white_time - self.move_overhead if self.white_time > self.move_overhead else self.white_time
                white_time /= 1000
                black_time = self.black_time / 1000
            else:
                black_time = self.black_time - self.move_overhead if self.black_time > self.move_overhead else self.black_time
                black_time /= 1000
                white_time = self.white_time / 1000
            increment = self.increment / 1000

            limit = chess.engine.Limit(white_clock=white_time, white_inc=increment,
                                       black_clock=black_time, black_inc=increment)
            ponder = self.config['engine']['ponder']

        result = self.engine.play(self.board, limit, info=chess.engine.INFO_ALL, ponder=ponder)
        if result.move:
            score = result.info.get('score', chess.engine.PovScore(chess.engine.Mate(1), self.board.turn))
            self.scores.append(score)
            return result.move, result.info
        raise RuntimeError('Engine could not make a move!')

    def _format_move(self, move: chess.Move) -> str:
        if self.board.turn:
            move_number = str(self.board.fullmove_number) + '.'
            return f'{move_number:4} {self.board.san(move)}'
        else:
            move_number = str(self.board.fullmove_number) + '...'
            return f'{move_number:6} {self.board.san(move)}'

    def _format_info(self, info: chess.engine.InfoDict) -> str:
        info_score = info.get('score')
        score = f'{self._format_score(info_score):7}' if info_score else 7 * ' '

        info_depth = info.get('depth')
        info_seldepth = info.get('seldepth')
        depth_str = f'{info_depth}/{info_seldepth}'
        depth = f'{depth_str:6}' if info_depth and info_seldepth else 6 * ' '

        info_nps = info.get('nps')
        nps = f'nps: {info_nps/1000000:5.1f} M' if info_nps else 8 * ' '

        info_time = info.get('time')
        time = f'mt: {info_time:5.1f} s' if info_time else 11 * ' '

        info_hashfull = info.get('hashfull')
        hashfull = f'hash: {info_hashfull/10:4.1f} %' if info_hashfull else 12 * ' '

        info_tbhits = info.get('tbhits')
        tbhits = f'tb: {info_tbhits}' if info_tbhits else ''

        return '     '.join((score, depth, nps, time, hashfull, tbhits))

    def _format_score(self, score: chess.engine.PovScore) -> str:
        if not score.is_mate():
            if cp_score := score.pov(self.board.turn).score():
                cp_score /= 100
                return format(cp_score, '+7.2f')
            else:
                return '   0.00'
        else:
            return str(score.pov(self.board.turn))

    def _get_engine(self) -> chess.engine.SimpleEngine:
        engine = chess.engine.SimpleEngine.popen_uci(self.config['engine']['path'])
        options = self.config['engine']['uci_options']

        def not_managed(key: str): return not chess.engine.Option(key, '', None, None, None, None).is_managed()
        options = {key: value for key, value in options.items() if not_managed(key)}

        engine.configure(options)

        return engine

    def _setup_board(self, gameFull_event: dict) -> chess.Board:
        if gameFull_event['variant']['key'] == 'chess960':
            board = chess.Board(gameFull_event['initialFen'], chess960=True)
        elif gameFull_event['variant']['name'] == 'From Position':
            board = chess.Board(gameFull_event['initialFen'])
        else:
            VariantBoard = find_variant(gameFull_event['variant']['name'])
            board = VariantBoard()

        for move in gameFull_event['state']['moves'].split():
            board.push_uci(move)

        return board

    def _get_move_overhead(self) -> int:
        multiplier = self.config.get('move_overhead_multiplier', 1.0)
        return int(self.initial_time / 60 * multiplier)

    def _has_time(self, min_time: int) -> bool:
        min_time *= 1000
        return self.white_time >= min_time if self.is_white else self.black_time >= min_time

    def _reduce_own_time(self, milliseconds: int):
        if self.is_white:
            self.white_time -= milliseconds
        else:
            self.black_time -= milliseconds
