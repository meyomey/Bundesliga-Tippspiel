"""KI-Tippgegner fuer die Wulmstörper Tipprunde.

Der Bot analysiert:
- Team-Form (letzte 5 Spiele)
- Tabellenposition
- Heim-/Auswärts-Stärke
- Historische Duelle
- Zufallsfaktor (realistisch unterschiedliche Tipps)

Schwierigkeitsgrade:
- EASY: Viel Zufall, einfache Heuristik
- MEDIUM: Balanciert aus Statistik und Zufall
- HARD: Starke Gewichtung auf Form und Tabelle
- EXPERT: Nutzt ML-Modell (falls trainiert)
"""
import random
import json
import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass

from flask import current_app

def poisson_random(lmbda: float) -> int:
    """Generiert eine Poisson-verteilte Zufallszahl (Knuth-Algorithmus)."""
    if lmbda <= 0:
        return 0
    L = math.exp(-lmbda)
    k = 0
    p = 1.0
    while p > L:
        k += 1
        p *= random.random()
    return max(0, k - 1)

from extensions import db
from models import Match, Team, Prediction, User


class Difficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


@dataclass
class TeamStats:
    """Statistiken fuer eine Mannschaft."""
    team_id: int
    team_name: str
    position: int
    points: int
    goals_scored: int
    goals_conceded: int
    home_wins: int
    home_draws: int
    home_losses: int
    away_wins: int
    away_draws: int
    away_losses: int
    form: List[str]  # ['W', 'D', 'L', 'W', 'W']
    
    @property
    def home_strength(self) -> float:
        """Berechnet Heimstärke (0-1)."""
        total = self.home_wins + self.home_draws + self.home_losses
        if total == 0:
            return 0.5
        return (self.home_wins * 3 + self.home_draws) / (total * 3)
    
    @property
    def away_strength(self) -> float:
        """Berechnet Auswärtsstärke (0-1)."""
        total = self.away_wins + self.away_draws + self.away_losses
        if total == 0:
            return 0.5
        return (self.away_wins * 3 + self.away_draws) / (total * 3)
    
    @property
    def form_score(self) -> float:
        """Berechnet Form-Score (0-1) aus letzten 5 Spielen."""
        if not self.form:
            return 0.5
        weights = [0.35, 0.28, 0.20, 0.12, 0.05]  # Letztes Spiel wichtiger
        score = 0
        for i, result in enumerate(self.form[:5]):
            w = weights[i] if i < len(weights) else 0.05
            if result == 'W':
                score += w
            elif result == 'D':
                score += w * 0.33
        return score


class AIOpponent:
    """KI-Tippgegner der gegen echte Spieler antritt."""
    
    def __init__(self, name: str, difficulty: Difficulty, user_id: int = None):
        self.name = name
        self.difficulty = difficulty
        self.user_id = user_id  # Falls als "User" in DB gespeichert
        self._model_trained = False
        self._team_stats_cache: Dict[int, TeamStats] = {}
    
    def get_tip(self, match: Match, all_matches: List[Match] = None) -> Tuple[int, int]:
        """Generiert Tipp fuer ein Spiel.
        
        Returns:
            Tuple (home_goals, away_goals)
        """
        # Statistiken sammeln
        home_stats = self._get_team_stats(match.home_team_id, all_matches)
        away_stats = self._get_team_stats(match.away_team_id, all_matches)
        
        # Basierend auf Schwierigkeitsgrad Tipps generieren
        if self.difficulty == Difficulty.EASY:
            return self._tip_easy(home_stats, away_stats, match)
        elif self.difficulty == Difficulty.MEDIUM:
            return self._tip_medium(home_stats, away_stats, match)
        elif self.difficulty == Difficulty.HARD:
            return self._tip_hard(home_stats, away_stats, match)
        else:  # EXPERT
            return self._tip_expert(home_stats, away_stats, match)
    
    def _get_team_stats(self, team_id: int, all_matches: List[Match] = None) -> TeamStats:
        """Holt oder berechnet Team-Statistiken."""
        if team_id in self._team_stats_cache:
            return self._team_stats_cache[team_id]
        
        team = Team.query.get(team_id)
        
        # Alle beendete Spiele des Teams holen
        if all_matches is None:
            all_matches = Match.query.filter(
                Match.status == "finished"
            ).order_by(Match.kickoff.desc()).all()
        
        team_matches = [
            m for m in all_matches 
            if m.home_team_id == team_id or m.away_team_id == team_id
        ]
        
        # Form berechnen (letzte 5)
        form = []
        for m in team_matches[:5]:
            if m.home_team_id == team_id:
                if m.home_score > m.away_score:
                    form.append('W')
                elif m.home_score == m.away_score:
                    form.append('D')
                else:
                    form.append('L')
            else:
                if m.away_score > m.home_score:
                    form.append('W')
                elif m.away_score == m.home_score:
                    form.append('D')
                else:
                    form.append('L')
        
        # Heim/Auswärts Bilanz
        home_matches = [m for m in team_matches if m.home_team_id == team_id]
        away_matches = [m for m in team_matches if m.away_team_id == team_id]
        
        home_wins = sum(1 for m in home_matches if m.home_score > m.away_score)
        home_draws = sum(1 for m in home_matches if m.home_score == m.away_score)
        home_losses = len(home_matches) - home_wins - home_draws
        
        away_wins = sum(1 for m in away_matches if m.away_score > m.home_score)
        away_draws = sum(1 for m in away_matches if m.away_score == m.home_score)
        away_losses = len(away_matches) - away_wins - away_draws
        
        # Tore
        goals_scored = sum(m.home_score for m in home_matches) + sum(m.away_score for m in away_matches)
        goals_conceded = sum(m.away_score for m in home_matches) + sum(m.home_score for m in away_matches)
        
        # Tabellenposition (simplifiziert - in echt aus Tabelle berechnen)
        points = home_wins * 3 + home_draws + away_wins * 3 + away_draws
        
        stats = TeamStats(
            team_id=team_id,
            team_name=team.name,
            position=0,  # Wird spaeter berechnet
            points=points,
            goals_scored=goals_scored,
            goals_conceded=goals_conceded,
            home_wins=home_wins,
            home_draws=home_draws,
            home_losses=home_losses,
            away_wins=away_wins,
            away_draws=away_draws,
            away_losses=away_losses,
            form=form
        )
        
        self._team_stats_cache[team_id] = stats
        return stats
    
    def _tip_easy(self, home: TeamStats, away: TeamStats, match: Match) -> Tuple[int, int]:
        """Einfacher Bot: Mehr Zufall, wenig Strategie."""
        # Basis: Heimvorteil
        home_prob = 0.5 + (random.random() * 0.2 - 0.1)  # 0.4 - 0.6
        
        # Zufälliges Ergebnis
        if random.random() < home_prob:
            home_goals = random.randint(1, 3)
            away_goals = random.randint(0, 2)
        else:
            home_goals = random.randint(0, 2)
            away_goals = random.randint(1, 3)
        
        return home_goals, away_goals
    
    def _tip_medium(self, home: TeamStats, away: TeamStats, match: Match) -> Tuple[int, int]:
        """Mittlerer Bot: Beruecksichtigt Form und Heimvorteil."""
        # Gewichtete Wahrscheinlichkeit
        home_advantage = 0.6
        form_factor = (home.form_score - away.form_score) * 0.3
        strength_factor = (home.home_strength - away.away_strength) * 0.2
        
        home_prob = home_advantage + form_factor + strength_factor
        home_prob = max(0.3, min(0.7, home_prob))  # Clamp
        
        # Ergebnis basierend auf Wahrscheinlichkeit
        if random.random() < home_prob * 0.6:  # Heimsieg
            home_goals = random.randint(2, 4)
            away_goals = random.randint(0, 2)
        elif random.random() < home_prob:  # Unentschieden
            home_goals = random.randint(1, 2)
            away_goals = home_goals
        else:  # Auswärtssieg
            home_goals = random.randint(0, 2)
            away_goals = random.randint(2, 4)
        
        return home_goals, away_goals
    
    def _tip_hard(self, home: TeamStats, away: TeamStats, match: Match) -> Tuple[int, int]:
        """Schwerer Bot: Starker Fokus auf Statistiken."""
        # Detaillierte Analyse
        home_strength = home.home_strength * 0.7 + home.form_score * 0.3
        away_strength = away.away_strength * 0.7 + away.form_score * 0.3
        
        # Erwartete Tore berechnen
        expected_home = 1.5 + (home_strength - 0.5) * 2
        expected_away = 1.0 + (away_strength - 0.5) * 2
        
        # Tordurchschnitte einbeziehen
        if home.goals_scored + home.goals_conceded > 0:
            home_avg = home.goals_scored / max(len(home.form), 1)
            expected_home = (expected_home + home_avg) / 2
        
        if away.goals_scored + away.goals_conceded > 0:
            away_avg = away.goals_scored / max(len(away.form), 1)
            expected_away = (expected_away + away_avg) / 2
        
        # Poisson-ähnliche Verteilung
        home_goals = poisson_random(expected_home)
        away_goals = poisson_random(expected_away)
        
        # Realistische Begrenzung
        home_goals = min(max(home_goals, 0), 5)
        away_goals = min(max(away_goals, 0), 5)
        
        return int(home_goals), int(away_goals)
    
    def _tip_expert(self, home: TeamStats, away: TeamStats, match: Match) -> Tuple[int, int]:
        """Expert-Bot: Beste Strategie + kleiner Zufallsfaktor."""
        # Wie HARD, aber mit optimierten Parametern
        home_strength = home.home_strength * 0.6 + home.form_score * 0.4
        away_strength = away.away_strength * 0.6 + away.form_score * 0.4
        
        # Historische Duelle
        h2h_home_wins = 0
        h2h_matches = Match.query.filter(
            Match.status == "finished",
            ((Match.home_team_id == home.team_id) & (Match.away_team_id == away.team_id)) |
            ((Match.home_team_id == away.team_id) & (Match.away_team_id == home.team_id))
        ).limit(5).all()
        
        if h2h_matches:
            for m in h2h_matches:
                if m.home_team_id == home.team_id and m.home_score > m.away_score:
                    h2h_home_wins += 1
                elif m.away_team_id == home.team_id and m.away_score > m.home_score:
                    h2h_home_wins += 1
            h2h_factor = (h2h_home_wins / len(h2h_matches) - 0.5) * 0.2
        else:
            h2h_factor = 0
        
        expected_home = 1.4 + (home_strength - 0.5) * 1.8 + h2h_factor
        expected_away = 1.1 + (away_strength - 0.5) * 1.5 - h2h_factor
        
        home_goals = poisson_random(expected_home)
        away_goals = poisson_random(expected_away)
        
        return min(max(int(home_goals), 0), 5), min(max(int(away_goals), 0), 5)


class AIManager:
    """Verwaltet alle KI-Gegner."""
    
    BOTS = [
        ("RookieBot", Difficulty.EASY),
        ("AmateurBot", Difficulty.EASY),
        ("ProBot", Difficulty.MEDIUM),
        ("ExpertBot", Difficulty.HARD),
        ("MasterBot", Difficulty.EXPERT),
    ]
    
    def __init__(self):
        self.opponents: List[AIOpponent] = []
        self._init_opponents()
    
    def _init_opponents(self):
        """Initialisiert Standard-Bots."""
        from utils import get_setting
        for name, diff in self.BOTS:
            # Suche oder erstelle User fuer Bot
            bot_user = User.query.filter_by(username=name).first()
            if not bot_user:
                bot_user = User(
                    username=name,
                    email=f"{name.lower()}@bot.local",
                    is_admin=False
                )
                bot_user.set_password(''.join(random.choices('0123456789abcdef', k=32)))
                db.session.add(bot_user)
                db.session.commit()
            
            # Difficulty aus den Settings laden
            saved_diff_val = get_setting(f"bot_difficulty_{name}", diff.value)
            try:
                bot_diff = Difficulty(saved_diff_val)
            except ValueError:
                bot_diff = diff
            
            self.opponents.append(AIOpponent(name, bot_diff, bot_user.id))

    def set_bot_difficulty(self, name: str, diff_value: str):
        """Setzt die Schwierigkeit eines Bots persistent."""
        from utils import set_setting
        opp = self.get_opponent(name)
        if opp:
            try:
                new_diff = Difficulty(diff_value)
                opp.difficulty = new_diff
                set_setting(f"bot_difficulty_{name}", diff_value)
                return True
            except ValueError:
                pass
        return False
    
    def get_opponent(self, name: str) -> Optional[AIOpponent]:
        """Holt einen spezifischen Bot."""
        for opp in self.opponents:
            if opp.name == name:
                return opp
        return None
    
    def tip_all_matches(self, matchday: int = None):
        """Laesst alle aktiven Bots Tipps fuer alle offenen Spiele abgeben."""
        from models import Prediction
        from utils import get_setting
        
        query = Match.query.filter(Match.status == "scheduled")
        if matchday:
            query = query.filter_by(matchday=matchday)
        matches = query.all()
        
        if not matches:
            return []
        
        # Alle beendete Spiele fuer Statistiken laden
        all_finished = Match.query.filter_by(status="finished").all()
        
        results = []
        for match in matches:
            for opponent in self.opponents:
                # Pruefe ob Bot aktiv ist
                is_active = get_setting(f"bot_active_{opponent.name}", True)
                if not is_active:
                    continue
                    
                # Pruefe ob Bot bereits getippt hat
                existing = Prediction.query.filter_by(
                    user_id=opponent.user_id,
                    match_id=match.id
                ).first()
                
                if not existing:
                    home_tip, away_tip = opponent.get_tip(match, all_finished)
                    
                    prediction = Prediction(
                        user_id=opponent.user_id,
                        match_id=match.id,
                        home_tip=home_tip,
                        away_tip=away_tip,
                        joker=False  # Bots nutzen keinen Joker
                    )
                    db.session.add(prediction)
                    results.append({
                        'bot': opponent.name,
                        'match': f"{match.home_team.name} vs {match.away_team.name}",
                        'tip': f"{home_tip}:{away_tip}"
                    })
        
        db.session.commit()
        return results
    
    def get_rankings(self) -> List[Dict]:
        """Liefert Rangliste der Bots."""
        rankings = []
        for opp in self.opponents:
            user = User.query.get(opp.user_id)
            if user:
                total_points = user.total_points()
                predictions_count = user.predictions.count()
                exact_count = sum(1 for p in user.predictions if p.points >= 4)
                
                rankings.append({
                    'name': opp.name,
                    'difficulty': opp.difficulty.value,
                    'points': total_points,
                    'predictions': predictions_count,
                    'exact': exact_count,
                    'avg_points': round(total_points / max(predictions_count, 1), 2)
                })
        
        return sorted(rankings, key=lambda x: x['points'], reverse=True)


# Singleton Instance
ai_manager = AIManager()


def get_ai_tip(bot_name: str, match_id: int) -> Tuple[int, int]:
    """Hilfsfunktion um schnell einen KI-Tipp zu bekommen."""
    opponent = ai_manager.get_opponent(bot_name)
    if not opponent:
        raise ValueError(f"Bot {bot_name} nicht gefunden")
    
    match = Match.query.get(match_id)
    if not match:
        raise ValueError(f"Spiel {match_id} nicht gefunden")
    
    return opponent.get_tip(match)
