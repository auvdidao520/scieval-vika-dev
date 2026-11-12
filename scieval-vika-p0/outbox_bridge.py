"""Single-request local stdio bridge for an existing P1 SQLite outbox."""
import json
import sqlite3
import sys
from pathlib import Path

MAX_REQUEST = 65536
MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 1024 * 1024


def main():
    if len(sys.argv) != 2:
        raise ValueError('expected database path')
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise ValueError('request too large')
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError('expected object')
    command = request.get('command')
    expected = {'command'} if command == 'pending' else {'command', 'event_id'}
    if command not in ('pending', 'acknowledge') or set(request) != expected:
        raise ValueError('invalid request')
    if command == 'acknowledge' and (not isinstance(request['event_id'], str) or not request['event_id'] or len(request['event_id']) > 1024):
        raise ValueError('invalid event id')
    # mode=rw prevents creating a database on a misspelled path. No schema migration.
    uri = Path(sys.argv[1]).resolve().as_uri() + '?mode=rw'
    db = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        if command == 'pending':
            events = []
            size = len('{"events":[]}')
            for event_id, job_id, revision, payload in db.execute('SELECT event_id, job_id, revision, payload FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT ?', (MAX_BATCH_EVENTS,)):
                event = json.loads(payload)
                if not isinstance(event, dict) or (event.get('event_id'), event.get('job_id'), event.get('revision')) != (event_id, job_id, revision):
                    raise ValueError('outbox payload identity mismatch')
                event_size = len(json.dumps(event, ensure_ascii=True, separators=(',', ':'))) + (1 if events else 0)
                if size + event_size > MAX_BATCH_BYTES:
                    if not events:
                        raise ValueError('outbox event exceeds batch size limit')
                    break
                events.append(event)
                size += event_size
            result = {'events': events}
        else:
            with db:
                changed = db.execute('UPDATE outbox SET delivered=1 WHERE event_id=?', (request['event_id'],)).rowcount
                if not changed:
                    raise ValueError('unknown event id')
            result = {'acknowledged': True}
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    finally:
        db.close()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
        print('outbox bridge error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
