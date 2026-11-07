# This file is part of the Tryton fuel_station module.
from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.wizard import Button, StateView, Wizard


class GuideStart(ModelView):
    "Usage Guide"
    __name__ = 'fuel.guide.start'

    content = fields.Text("Guide", readonly=True,
        help="Text of the usage guide.")


class Guide(Wizard):
    "Usage Guide"
    __name__ = 'fuel.guide'

    start = StateView(
        'fuel.guide.start', 'fuel_station.guide_start_view_form', [
            Button("Close", 'end', 'tryton-close', default=True),
            ])

    def default_start(self, fields):
        return {'content': gettext('fuel_station.msg_guide_usage')}
