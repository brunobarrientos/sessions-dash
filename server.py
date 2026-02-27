#!/usr/bin/env python3
"""Sessions Dashboard — lightweight Claude Code token usage tracker.

Parses ~/.claude/projects/ JSONL session files and displays token usage
and estimated costs. No dependencies beyond Python stdlib.

Port: 8766
"""

import http.server
import json
import os
import glob
import socketserver
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from http import HTTPStatus

PORT = 8766
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Model pricing ($/1M tokens) ──────────────────────────────────────
MODEL_PRICING = {
    'MiniMax-M2.5':               {'input': 0.30, 'output': 1.20, 'cacheRead': 0.03, 'cacheWrite': 0.30},
    'MiniMax-M2.1':               {'input': 0.30, 'output': 1.20, 'cacheRead': 0.03, 'cacheWrite': 0.30},
    'k2p5':                       {'input': 0.60, 'output': 2.50, 'cacheRead': 0.10, 'cacheWrite': 0.60},
    'kimi-k2.5':                  {'input': 0.60, 'output': 2.50, 'cacheRead': 0.10, 'cacheWrite': 0.60},
    'glm-4.7':                    {'input': 0.60, 'output': 2.20, 'cacheRead': 0.11, 'cacheWrite': 0.60},
    'claude-opus-4-6':            {'input': 15.00, 'output': 75.00, 'cacheRead': 1.50, 'cacheWrite': 15.00},
    'claude-sonnet-4-6':          {'input': 3.00, 'output': 15.00, 'cacheRead': 0.30, 'cacheWrite': 3.75},
    'claude-sonnet-4-5-20250929': {'input': 3.00, 'output': 15.00, 'cacheRead': 0.30, 'cacheWrite': 3.75},
    'claude-3-5-sonnet-20241022': {'input': 3.00, 'output': 15.00, 'cacheRead': 0.30, 'cacheWrite': 3.75},
    'claude-3-5-haiku-20241022':  {'input': 0.80, 'output': 4.00, 'cacheRead': 0.08, 'cacheWrite': 0.80},
    'claude-haiku-4-5-20251001':  {'input': 0.80, 'output': 4.00, 'cacheRead': 0.08, 'cacheWrite': 0.80},
}
DEFAULT_PRICING = {'input': 1.00, 'output': 5.00, 'cacheRead': 0.10, 'cacheWrite': 1.00}


def _cost(model, usage):
    """Calculate cost for a model's token usage."""
    rates = MODEL_PRICING.get(model, DEFAULT_PRICING)
    net_input = max(0, usage['input'] - usage['cacheRead'] - usage['cacheWrite'])
    return (
        net_input * rates['input'] / 1_000_000
        + usage['output'] * rates['output'] / 1_000_000
        + usage['cacheRead'] * rates['cacheRead'] / 1_000_000
        + usage['cacheWrite'] * rates['cacheWrite'] / 1_000_000
    )


def _decode_project_folder(folder_name):
    """Decode Claude Code project folder name to human-readable path."""
    home = os.path.expanduser('~')
    home_encoded = '-' + home.replace('/', '-')[1:]
    if folder_name == home_encoded:
        return '~'
    prefix = home_encoded + '-'
    if folder_name.startswith(prefix):
        remainder = folder_name[len(prefix):]
        # Try to reconstruct path by probing filesystem
        parts = remainder.split('-')
        best = ''
        path = home
        i = 0
        while i < len(parts):
            # Try progressively longer segments (handles dirs with hyphens)
            for j in range(len(parts), i, -1):
                candidate = '-'.join(parts[i:j])
                test_path = os.path.join(path, candidate)
                if os.path.exists(test_path):
                    path = test_path
                    best = path
                    i = j
                    break
            else:
                # No match found, append remainder as-is
                remaining = '-'.join(parts[i:])
                return f'~/{os.path.relpath(path, home)}/{remaining}' if path != home else f'~/{remaining}'
        return '~/' + os.path.relpath(best, home) if best else folder_name
    return folder_name


def compute_usage(days=7):
    """Compute Claude Code usage stats by model and by day."""
    claude_dir = os.path.expanduser('~/.claude/projects')
    if not os.path.isdir(claude_dir):
        return {'byModel': {}, 'byDay': [], 'totalEstimatedCost': 0, 'totalSessions': 0, 'days': days}

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    by_model = {}
    by_day = {}
    sessions_by_day = {}

    for project_folder in os.listdir(claude_dir):
        project_path = os.path.join(claude_dir, project_folder)
        if not os.path.isdir(project_path):
            continue
        for jsonl_file in glob.glob(os.path.join(project_path, '*.jsonl')):
            try:
                mtime = os.path.getmtime(jsonl_file)
                if datetime.fromtimestamp(mtime) < cutoff:
                    continue
                session_day = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                sessions_by_day[session_day] = sessions_by_day.get(session_day, 0) + 1

                with open(jsonl_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '"usage"' not in line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = entry.get('timestamp', '')
                        if ts and ts[:10] < cutoff_str:
                            continue
                        msg = entry.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        usage = msg.get('usage')
                        if not usage:
                            continue
                        model = entry.get('model') or msg.get('model', 'unknown')
                        if model.startswith('<'):
                            continue
                        day_str = ts[:10] if ts else session_day

                        for bucket in (by_model, by_day.setdefault(day_str, {})):
                            if model not in bucket:
                                bucket[model] = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}
                        by_model[model]['input'] += usage.get('input_tokens', 0)
                        by_model[model]['output'] += usage.get('output_tokens', 0)
                        by_model[model]['cacheRead'] += usage.get('cache_read_input_tokens', 0)
                        by_model[model]['cacheWrite'] += usage.get('cache_creation_input_tokens', 0)
                        by_day[day_str][model]['input'] += usage.get('input_tokens', 0)
                        by_day[day_str][model]['output'] += usage.get('output_tokens', 0)
                        by_day[day_str][model]['cacheRead'] += usage.get('cache_read_input_tokens', 0)
                        by_day[day_str][model]['cacheWrite'] += usage.get('cache_creation_input_tokens', 0)
            except Exception:
                continue

    # Filter empty/synthetic models
    by_model = {m: u for m, u in by_model.items() if (u['input'] + u['output']) > 0}

    # Calculate costs
    for model, usage in by_model.items():
        usage['estimatedCost'] = round(_cost(model, usage), 4)

    by_day_list = sorted(
        [{'date': d, 'models': {m: u for m, u in models.items() if (u['input'] + u['output']) > 0},
          'sessions': sessions_by_day.get(d, 0)}
         for d, models in by_day.items() if models],
        key=lambda x: x['date'], reverse=True
    )

    return {
        'byModel': by_model,
        'byDay': by_day_list,
        'totalEstimatedCost': round(sum(u['estimatedCost'] for u in by_model.values()), 2),
        'totalSessions': sum(sessions_by_day.values()),
        'days': days,
    }


def compute_sessions(days=7, limit=50):
    """Compute per-session cost breakdown."""
    claude_dir = os.path.expanduser('~/.claude/projects')
    if not os.path.isdir(claude_dir):
        return {'sessions': [], 'total': 0, 'days': days}

    cutoff = datetime.now() - timedelta(days=days)
    sessions = []

    for project_folder in os.listdir(claude_dir):
        project_path = os.path.join(claude_dir, project_folder)
        if not os.path.isdir(project_path):
            continue
        project_display = _decode_project_folder(project_folder)

        for jsonl_file in glob.glob(os.path.join(project_path, '*.jsonl')):
            try:
                mtime = os.path.getmtime(jsonl_file)
                if datetime.fromtimestamp(mtime) < cutoff:
                    continue
                session_id = os.path.basename(jsonl_file).replace('.jsonl', '')
                by_model = {}
                first_ts = last_ts = None
                msg_count = 0

                with open(jsonl_file, 'r') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        has_ts = '"timestamp"' in line
                        has_usage = '"usage"' in line
                        has_type = '"type"' in line
                        if not has_ts and not has_usage and not has_type:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if has_ts:
                            ts = entry.get('timestamp', '')
                            if ts:
                                if not first_ts:
                                    first_ts = ts
                                last_ts = ts
                        if has_type and entry.get('type') in ('user', 'assistant'):
                            msg_count += 1
                        if not has_usage:
                            continue
                        msg = entry.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        usage = msg.get('usage')
                        if not usage:
                            continue
                        model = entry.get('model') or msg.get('model', 'unknown')
                        if model.startswith('<'):
                            continue
                        if model not in by_model:
                            by_model[model] = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}
                        by_model[model]['input'] += usage.get('input_tokens', 0)
                        by_model[model]['output'] += usage.get('output_tokens', 0)
                        by_model[model]['cacheRead'] += usage.get('cache_read_input_tokens', 0)
                        by_model[model]['cacheWrite'] += usage.get('cache_creation_input_tokens', 0)

                by_model = {m: u for m, u in by_model.items() if (u['input'] + u['output']) > 0}
                if not by_model:
                    continue

                total_cost = sum(_cost(m, u) for m, u in by_model.items())
                total_input = sum(u['input'] for u in by_model.values())
                total_output = sum(u['output'] for u in by_model.values())
                primary_model = max(by_model, key=lambda m: by_model[m]['input'] + by_model[m]['output'])

                sessions.append({
                    'sessionId': session_id,
                    'project': project_display,
                    'date': (last_ts or first_ts or datetime.fromtimestamp(mtime).isoformat())[:10],
                    'primaryModel': primary_model,
                    'totalInput': total_input,
                    'totalOutput': total_output,
                    'msgCount': msg_count,
                    'estimatedCost': round(total_cost, 4),
                    'mtime': mtime,
                })
            except Exception:
                continue

    sessions.sort(key=lambda s: s['mtime'], reverse=True)
    return {'sessions': sessions[:limit], 'total': len(sessions), 'days': days}


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Silence request logs

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == '/api/usage':
            days = int(params.get('days', ['7'])[0])
            self._json(compute_usage(days))
        elif parsed.path == '/api/sessions':
            days = int(params.get('days', ['7'])[0])
            limit = int(params.get('limit', ['50'])[0])
            self._json(compute_sessions(days, limit))
        elif parsed.path in ('/', '/index.html'):
            self._serve_file('index.html')
        elif parsed.path == '/health':
            self._json({'ok': True})
        else:
            self.send_error(404)

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename):
        filepath = os.path.join(SCRIPT_DIR, filename)
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    with ThreadedServer(('0.0.0.0', PORT), Handler) as httpd:
        print(f'Sessions Dashboard running on http://0.0.0.0:{PORT}')
        httpd.serve_forever()
