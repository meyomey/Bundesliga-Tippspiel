"""Live-Scoring Funktionalitaet fuer die Wulmstörper Tipprunde.

Bietet:
- Live-Spiel-Updates via Server-Sent Events (SSE)
- Websocket-Alternative fuer Echtzeit-Updates
- REST API Polling Endpunkte
- Automatische Status-Aenderung (scheduled -> live -> finished)
"""
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from flask import Blueprint, jsonify, current_app, Response, stream_with_context
from flask_login import login_required, current_user

from extensions import db, cache
from models import Match, Prediction, User, Competition
from cache import cache_key_match_detail, invalidate_match

# Blueprint fuer Live-Scoring Routes
live_bp = Blueprint('live', __name__)


class LiveMatchManager:
    """Verwaltet Live-Spiele und deren Updates."""
    
    def __init__(self):
        self._active_matches: Dict[int, dict] = {}
    
    def get_live_matches(self, competition_id: int = None) -> List[dict]:
        """Holt alle aktuell laufenden Spiele."""
        # Cache fuer 30 Sekunden
        cache_key = f"live_matches:{competition_id or 'all'}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        query = Match.query.filter(
            Match.status.in_(['live', 'in_progress']),
            Match.is_live == True
        )
        
        if competition_id:
            query = query.filter_by(competition_id=competition_id)
        
        matches = query.order_by(Match.kickoff.desc()).all()
        
        result = []
        for match in matches:
            result.append(self._format_live_match(match))
        
        cache.set(cache_key, result, ttl=30)
        return result
    
    def _format_live_match(self, match: Match) -> dict:
        """Formatiert ein Match fuer Live-Ansicht."""
        events = []
        if match.events:
            try:
                events = json.loads(match.events)
            except:
                events = []
        
        return {
            'id': match.id,
            'competition': match.competition.name if match.competition else None,
            'matchday': match.matchday,
            'home_team': {
                'id': match.home_team.id,
                'name': match.home_team.name,
                'short_name': match.home_team.short_name,
                'logo': match.home_team.logo,
            },
            'away_team': {
                'id': match.away_team.id,
                'name': match.away_team.name,
                'short_name': match.away_team.short_name,
                'logo': match.away_team.logo,
            },
            'score': {
                'home': match.home_score or 0,
                'away': match.away_score or 0,
            },
            'minute': match.minute or 0,
            'status': match.status,
            'events': events,
            'kickoff': match.kickoff.isoformat() if match.kickoff else None,
        }
    
    def update_match(self, match_id: int, home_score: int, away_score: int, 
                     minute: int = None, events: list = None) -> bool:
        """Aktualisiert ein Live-Spiel."""
        match = Match.query.get(match_id)
        if not match:
            return False
        
        match.home_score = home_score
        match.away_score = away_score
        match.minute = minute
        match.is_live = True
        
        if events:
            match.events = json.dumps(events)
        
        if match.status == 'scheduled':
            match.status = 'live'
        
        db.session.commit()
        
        # Cache invalidieren
        invalidate_match(match_id)
        cache.delete(f"live_matches:*")
        # Live-Leaderboard NICHT cachen, aber falls doch:
        cache.delete_pattern("leaderboard:*")
        
        return True
    
    def finish_match(self, match_id: int, home_score: int, away_score: int) -> bool:
        """Beendet ein Spiel und berechnet Punkte."""
        from utils import calculate_points
        
        match = Match.query.get(match_id)
        if not match:
            return False
        
        match.home_score = home_score
        match.away_score = away_score
        match.status = 'finished'
        match.is_live = False
        match.minute = 90
        
        # Punkte fuer alle Tipps berechnen
        for prediction in match.predictions:
            prediction.points = calculate_points(prediction, match)
        
        db.session.commit()
        
        # Caches invalidieren
        invalidate_match(match_id)
        cache.delete(f"live_matches:*")
        from cache import invalidate_leaderboard
        invalidate_leaderboard()
        
        return True
    
    def get_match_stats(self, match_id: int) -> Optional[dict]:
        """Holt detaillierte Statistiken fuer ein Spiel."""
        match = Match.query.get(match_id)
        if not match:
            return None
        
        # Alle Tipps fuer dieses Spiel
        predictions = Prediction.query.filter_by(match_id=match_id).all()
        
        tip_distribution = {'1': 0, 'X': 0, '2': 0}
        for pred in predictions:
            if pred.home_tip > pred.away_tip:
                tip_distribution['1'] += 1
            elif pred.home_tip == pred.away_tip:
                tip_distribution['X'] += 1
            else:
                tip_distribution['2'] += 1
        
        return {
            'match': self._format_live_match(match),
            'predictions_count': len(predictions),
            'tip_distribution': tip_distribution,
            'top_tips': self._get_top_tips(match_id),
        }
    
    def _get_top_tips(self, match_id: int, limit: int = 5) -> List[dict]:
        """Holt die besten Tipps fuer ein Spiel."""
        if Match.query.get(match_id).status != 'finished':
            return []
        
        top_predictions = Prediction.query.filter_by(match_id=match_id)\
            .order_by(Prediction.points.desc())\
            .limit(limit).all()
        
        return [
            {
                'user': pred.user.username,
                'tip': f"{pred.home_tip}:{pred.away_tip}",
                'points': pred.points,
                'joker': pred.joker
            }
            for pred in top_predictions
        ]


# Singleton Instance
live_manager = LiveMatchManager()


# =================================================================== API Routes -

@live_bp.route('/matches')
def live_matches():
    """API: Alle aktuell laufenden Spiele."""
    competition_id = request.args.get('competition', type=int)
    matches = live_manager.get_live_matches(competition_id)
    return jsonify({
        'matches': matches,
        'count': len(matches),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@live_bp.route('/match/<int:match_id>')
def live_match_detail(match_id: int):
    """API: Details zu einem Live-Spiel."""
    stats = live_manager.get_match_stats(match_id)
    if not stats:
        return jsonify({'error': 'Match not found'}), 404
    return jsonify(stats)


@live_bp.route('/match/<int:match_id>/stream')
def live_match_stream(match_id: int):
    """Server-Sent Events fuer Live-Updates eines Spiels."""
    def event_stream():
        last_update = None
        while True:
            match = Match.query.get(match_id)
            if not match or match.status == 'finished':
                yield f"data: {json.dumps({'status': 'finished'})}\n\n"
                break
            
            # Pruefe auf Updates
            current_data = live_manager._format_live_match(match)
            if current_data != last_update:
                yield f"data: {json.dumps(current_data)}\n\n"
                last_update = current_data
            
            import time
            time.sleep(5)  # Alle 5 Sekunden pruefen
    
    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@live_bp.route('/user/predictions')
@login_required
def user_live_predictions():
    """API: Live-Status der eigenen Tipps."""
    # Alle Tipps des aktuellen Users fuer laufende Spiele
    predictions = Prediction.query.join(Match).filter(
        Prediction.user_id == current_user.id,
        Match.is_live == True
    ).all()
    
    result = []
    for pred in predictions:
        result.append({
            'match_id': pred.match_id,
            'match': f"{pred.match.home_team.name} vs {pred.match.away_team.name}",
            'my_tip': f"{pred.home_tip}:{pred.away_tip}",
            'current_score': f"{pred.match.home_score or 0}:{pred.match.away_score or 0}",
            'minute': pred.match.minute or 0,
            'potential_points': pred.points if pred.match.status == 'finished' else None
        })
    
    return jsonify({'predictions': result})


# =================================================================== Admin Routes -

@live_bp.route('/admin/update/<int:match_id>', methods=['POST'])
@login_required
def admin_update_match(match_id: int):
    """Admin: Manuelles Update eines Live-Spiels."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json() or request.form
    
    success = live_manager.update_match(
        match_id=match_id,
        home_score=data.get('home_score', type=int),
        away_score=data.get('away_score', type=int),
        minute=data.get('minute', type=int),
        events=data.get('events', type=list)
    )
    
    if success:
        return jsonify({'success': True, 'message': 'Match updated'})
    return jsonify({'error': 'Update failed'}), 400


@live_bp.route('/admin/finish/<int:match_id>', methods=['POST'])
@login_required
def admin_finish_match(match_id: int):
    """Admin: Spiel beenden und Punkte berechnen."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json() or request.form
    
    success = live_manager.finish_match(
        match_id=match_id,
        home_score=data.get('home_score', type=int),
        away_score=data.get('away_score', type=int)
    )
    
    if success:
        return jsonify({
            'success': True, 
            'message': 'Match finished and points calculated'
        })
    return jsonify({'error': 'Finish failed'}), 400


# Import fuer Routes
from flask import request
