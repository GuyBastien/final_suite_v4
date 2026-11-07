# This file is part of the Tryton fuel_station module.
import csv
import datetime
import io

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.wizard import Button, StateTransition, StateView, Wizard

from .exceptions import ShiftError

SOURCES = [
    ('manual', "Manual"),
    ('import', "File Import"),
    ('device', "Pump Controller"),
    ]


class MeterReading(ModelSQL, ModelView):
    "Fuel Meter Reading"
    __name__ = 'fuel.meter.reading'

    nozzle = fields.Many2One('fuel.nozzle', "Nozzle", required=True,
        help="Nozzle of the pump used.")
    station = fields.Function(
        fields.Many2One('fuel.station', "Station",
        help="Station concerned."), 'get_station',
        searcher='search_station')
    timestamp = fields.DateTime("Date and Time", required=True,
        help="Date and time of the event.")
    index = fields.Float(
        "Index", digits=(16, 3), required=True,
        domain=[('index', '>=', 0)],
        help="Value of the totalizer (gallons).")
    source = fields.Selection(SOURCES, "Source", required=True,
        help="Where the record comes from.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('timestamp', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))

    @classmethod
    def default_timestamp(cls):
        return datetime.datetime.now()

    @classmethod
    def default_source(cls):
        return 'manual'

    def get_station(self, name):
        return self.nozzle.station.id

    @classmethod
    def search_station(cls, name, clause):
        return [('nozzle.pump.station',) + tuple(clause[1:])]

    @classmethod
    def record(cls, station, pump, number, index, source='device',
            timestamp=None, company=None):
        """Register a reading from an external system (pump controller,
        forecourt controller...). `station` and `pump` are codes."""
        Nozzle = Pool().get('fuel.nozzle')
        domain = [
            ('pump.station.code', '=', station),
            ('pump.code', '=', pump),
            ('number', '=', int(number)),
            ]
        company = company or Transaction().context.get('company')
        if company:
            domain.append(('pump.station.company', '=', company))
        nozzles = Nozzle.search(domain, limit=1)
        if not nozzles:
            raise ShiftError(gettext(
                    'fuel_station.msg_reading_unknown_nozzle',
                    station=station, pump=pump, number=number))
        reading, = cls.create([{
                    'nozzle': nozzles[0].id,
                    'index': float(index),
                    'source': source,
                    'timestamp': timestamp or datetime.datetime.now(),
                    }])
        return reading


class ImportReadingsStart(ModelView):
    "Import Meter Readings"
    __name__ = 'fuel.meter.reading.import.start'

    file = fields.Binary("File", required=True,
        help="CSV file with the columns: station, pump, nozzle, index "
        "(separator comma or semicolon, header line optional).")


class ImportReadings(Wizard):
    "Import Meter Readings"
    __name__ = 'fuel.meter.reading.import'

    start = StateView(
        'fuel.meter.reading.import.start',
        'fuel_station.meter_reading_import_start_view_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Import", 'import_', 'tryton-ok', default=True),
            ])
    import_ = StateTransition()

    def transition_import_(self):
        Reading = Pool().get('fuel.meter.reading')
        data = self.start.file
        if isinstance(data, (bytes, bytearray)):
            data = data.decode('utf-8-sig')
        dialect = csv.Sniffer().sniff(data[:1024], delimiters=',;\t')
        rows = list(csv.reader(io.StringIO(data), dialect))
        now = datetime.datetime.now()
        for row in rows:
            row = [c.strip() for c in row]
            if len(row) < 4:
                continue
            try:
                float(row[3].replace(',', '.'))
            except ValueError:
                continue  # header
            Reading.record(
                row[0], row[1], row[2], row[3].replace(',', '.'),
                source='import', timestamp=now)
        return 'end'
