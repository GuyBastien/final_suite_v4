# This file is part of the Tryton hotel_channel_manager module.
"Minimal iCalendar (RFC 5545) reader and writer for booking calendars"
import datetime

CRLF = '\r\n'


def _unfold(text):
    lines = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value):
    return (value.replace('\\n', '\n').replace('\\N', '\n')
        .replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\'))


def _escape(value):
    return (value.replace('\\', '\\\\').replace(';', '\\;')
        .replace(',', '\\,').replace('\n', '\\n'))


def parse_value(value):
    """Return a date (all day value) or a naive datetime.

    Times ending with Z are UTC; the zone is dropped and the time is kept
    as it is, calendars of a hotel are expected in its local time.
    """
    value = value.strip()
    if 'T' in value:
        value = value.rstrip('Z')
        for fmt in ('%Y%m%dT%H%M%S', '%Y%m%dT%H%M'):
            try:
                return datetime.datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise ValueError("Invalid date-time: %r" % value)
    return datetime.datetime.strptime(value, '%Y%m%d').date()


def parse(text):
    """Return the events of the calendar as a list of dictionaries with the
    keys uid, summary, start, end, status and description."""
    events = []
    current = None
    for line in _unfold(text):
        if not line.strip():
            continue
        if line.strip().upper() == 'BEGIN:VEVENT':
            current = {}
            continue
        if line.strip().upper() == 'END:VEVENT':
            if current is not None:
                events.append(_finish(current))
            current = None
            continue
        if current is None or ':' not in line:
            continue
        name, value = line.split(':', 1)
        name = name.split(';', 1)[0].strip().upper()
        current.setdefault(name, value)
    return [e for e in events if e]


def _finish(raw):
    if 'DTSTART' not in raw or 'UID' not in raw:
        return None
    start = parse_value(raw['DTSTART'])
    if 'DTEND' in raw:
        end = parse_value(raw['DTEND'])
    elif isinstance(start, datetime.datetime):
        end = start + datetime.timedelta(hours=1)
    else:
        end = start + datetime.timedelta(days=1)
    return {
        'uid': raw['UID'].strip(),
        'summary': _unescape(raw.get('SUMMARY', '')).strip(),
        'description': _unescape(raw.get('DESCRIPTION', '')).strip(),
        'status': raw.get('STATUS', 'CONFIRMED').strip().upper(),
        'start': start,
        'end': end,
        }


def build(events, name="Hotel", now=None):
    """Return the text of a calendar for events given as dictionaries with
    the keys uid, summary, start and end (dates)."""
    now = now or datetime.datetime.utcnow()
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Tryton//hotel_channel_manager//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:' + _escape(name),
        ]
    for event in events:
        lines += [
            'BEGIN:VEVENT',
            'UID:' + event['uid'],
            'DTSTAMP:' + now.strftime('%Y%m%dT%H%M%SZ'),
            'DTSTART;VALUE=DATE:' + event['start'].strftime('%Y%m%d'),
            'DTEND;VALUE=DATE:' + event['end'].strftime('%Y%m%d'),
            'SUMMARY:' + _escape(event.get('summary', 'Reserved')),
            'TRANSP:OPAQUE',
            'END:VEVENT',
            ]
    lines.append('END:VCALENDAR')
    return CRLF.join(_fold(l) for l in lines) + CRLF


def _fold(line):
    "Lines are limited to 75 octets"
    data = line.encode('utf-8')
    if len(data) <= 75:
        return line
    parts = []
    while len(data) > 75:
        cut = 75
        while cut > 0 and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(data[:cut].decode('utf-8'))
        data = b' ' + data[cut:]
    parts.append(data.decode('utf-8'))
    return CRLF.join(parts)
